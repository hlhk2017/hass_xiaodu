import asyncio
import logging

from homeassistant import core
from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
    ATTR_COLOR_TEMP_KELVIN,
    LightEntityFeature,
    ATTR_EFFECT
)
from homeassistant.util.color import color_temperature_kelvin_to_mired as kelvin_to_mired
from . import XiaoDuAPI, ApplianceTypes
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: core.HomeAssistant, config_entry, async_add_entities):
    api = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    A = ApplianceTypes()
    for device_id in api:
        aapi: XiaoDuAPI = api[device_id]
        # 判断是否是light设备
        applianceTypes = aapi.applianceTypes
        if not A.is_light(applianceTypes):
            continue
        detail = await aapi.get_detail()
        if detail == []:
            continue
        name = detail['appliance']['friendlyName']
        if_onS = str(detail['appliance']['stateSetting']['turnOnState']['value']).lower()
        if if_onS == "on":
            if_on = True
        else:
            if_on = False
        entities.append(XiaoDuLight(api[device_id], name, if_on, detail['appliance']))
    async_add_entities(entities, update_before_add=True)


class XiaoDuLight(LightEntity):
    def __init__(self, api: XiaoDuAPI, name: str, if_on: bool, detail):
        self._api = api
        self._attr_unique_id = f"{api.applianceId}_light"
        self._attr_is_on = if_on
        self._attr_name = name
        self._group_name = detail['groupName']
        self.pColorMode = None
        self.effectList = {}
        self._color_temp_kelvin = None  # 初始化色温属性

        if if_on:
            self._attr_icon = "mdi:lightbulb"
        else:
            self._attr_icon = "mdi:lightbulb-off"

        # 设置支持的颜色模式
        if 'brightness' in detail['stateSetting'] and 'colorTemperatureInKelvin' in detail['stateSetting']:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self.pColorMode = ColorMode.COLOR_TEMP
        elif 'brightness' in detail['stateSetting']:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self.pColorMode = ColorMode.BRIGHTNESS
        
        # 处理灯光模式效果
        if 'mode' in detail['stateSetting']:
            self._attr_supported_features = LightEntityFeature.EFFECT
            effect_list = []
            valueRangeMap = detail['stateSetting']['mode']['valueRangeMap']
            for i in valueRangeMap:
                effect_list.append(valueRangeMap[i])
            self._attr_effect_list = effect_list

        # 默认只支持开关
        if self.pColorMode is None:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF
            self.pColorMode = ColorMode.ONOFF

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._color_temp_kelvin

    async def async_turn_on(self, **kwargs):
        # 基础开关控制
        if not kwargs:
            flag = await self._api.switch_on()
        else:
            # 亮度控制
            if ATTR_BRIGHTNESS in kwargs:
                brightness = kwargs[ATTR_BRIGHTNESS]
                attributeValue = round(brightness / 255 * 100)
                self._attr_brightness = brightness
                flag = await self._api.brightness(attributeValue)
            
            # 色温控制（使用新的属性）
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                color_temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
                self._attr_color_temp_kelvin = color_temp_kelvin
                self._color_temp_kelvin = color_temp_kelvin
                
                # 计算色温比例
                mddile = self.max_color_temp_kelvin - self.min_color_temp_kelvin
                attributeValue = round((color_temp_kelvin - self.min_color_temp_kelvin) / mddile * 100)
                flag = await self._api.colorTemperatureInKelvin(attributeValue)
            
            # 效果模式控制
            if ATTR_EFFECT in kwargs:
                effect = kwargs[ATTR_EFFECT]
                mode = "READING"  # 默认模式
                for key, value in self.effectList.items():
                    if value == effect:
                        mode = key
                        break
                flag = await self._api.light_set_mode(mode)

        # 更新状态
        self._attr_is_on = True
        self._attr_icon = "mdi:lightbulb"
        self.async_schedule_update_ha_state(True)

    async def async_turn_off(self, **kwargs):
        flag = await self._api.switch_off()
        self._attr_is_on = False
        self._attr_icon = "mdi:lightbulb-off"
        self.async_schedule_update_ha_state(True)
        
        # 控制失败时回退状态
        if not flag:
            self._attr_is_on = True
            self._attr_icon = "mdi:lightbulb"
            self.async_schedule_update_ha_state(True)

    async def async_update(self):
        await asyncio.sleep(1)
        await asyncio.create_task(self.amen_update())

    async def amen_update(self):
        detail = await self._api.get_detail()
        if not detail:
            return
            
        detail = detail['appliance']
        # 更新开关状态
        turnOnState = str(detail['stateSetting']['turnOnState']['value']).lower() == "on"
        self._attr_is_on = turnOnState

        # 更新效果模式列表
        if 'mode' in detail['stateSetting']:
            self.effectList = detail['stateSetting']['mode']['valueRangeMap']
            self._attr_supported_features = LightEntityFeature.EFFECT
            effect_list = [v for v in self.effectList.values()]
            self._attr_effect_list = effect_list
            
            # 更新当前效果模式
            if 'value' in detail['stateSetting']['mode']:
                mode = detail['stateSetting']['mode']['value']
                self._attr_effect = self.effectList.get(mode)

        # 更新亮度（确保转换为整数）
        if self.pColorMode in (ColorMode.BRIGHTNESS, ColorMode.COLOR_TEMP) and 'brightness' in detail['stateSetting']:
            brightness = detail['stateSetting']['brightness']['value']
            # 修复类型错误：将字符串转换为整数
            try:
                brightness_int = int(brightness)
                self._attr_brightness = round(brightness_int / 100 * 255)
            except (ValueError, TypeError) as e:
                _LOGGER.error(f"无法转换亮度值: {brightness}, 错误: {e}")

        # 更新色温
        if self.pColorMode == ColorMode.COLOR_TEMP and 'colorTemperatureInKelvin' in detail['stateSetting']:
            # 获取色温比例和范围
            color_temp_ratio = detail['stateSetting']['colorTemperatureInKelvin']['value']
            try:
                color_temp_ratio = int(color_temp_ratio)
            except (ValueError, TypeError) as e:
                _LOGGER.error(f"无法转换色温比例: {color_temp_ratio}, 错误: {e}")
                return

            # 色温范围
            temp_range = detail['stateSetting']['colorTemperatureInKelvin']['valueKelvinRangeMap']
            min_kelvin = temp_range.get('min', 2700)
            max_kelvin = temp_range.get('max', 6500)
            
            # 更新色温属性
            self._attr_min_color_temp_kelvin = min_kelvin
            self._attr_max_color_temp_kelvin = max_kelvin
            self._attr_min_mireds = kelvin_to_mired(min_kelvin)
            self._attr_max_mireds = kelvin_to_mired(max_kelvin)
            
            # 计算实际色温值
            mddile = max_kelvin - min_kelvin
            color_temp_kelvin = round((color_temp_ratio / 100 * mddile) + min_kelvin)
            self._attr_color_temp_kelvin = color_temp_kelvin
            self._color_temp_kelvin = color_temp_kelvin
