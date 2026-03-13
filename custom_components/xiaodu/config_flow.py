import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN, CONF_BAIDUID_COOKIE
from .api.XiaoDuAPI import XiaoDuAPI

_LOGGER = logging.getLogger(__name__)

class XiaoduConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.cookie = None
        self._home_id_list = None
        self.home_id = None
        self._device_wifi_id_dict = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            self.cookie = user_input[CONF_BAIDUID_COOKIE]
            session = async_get_clientsession(self.hass)
            xiaoduApi = XiaoDuAPI(self.cookie, session)
            try:
                loginFlag = await xiaoduApi.checkSession()
                if not loginFlag[0]:
                    errors["base"] = "invalid_auth"
                else:
                    self._home_id_list = await xiaoduApi.get_home_id_list()
                    if not self._home_id_list:
                        errors["base"] = "no_homes"
                    else:
                        return await self.async_step_home()
            except Exception as e:
                _LOGGER.error("Error checking session: %s", e)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_BAIDUID_COOKIE): str}),
            description_placeholders={"BAIDU_COOKIE_hint": "app login BAIDU_COOKIE"},
            errors=errors,
        )

    async def async_step_home(self, user_input=None):
        errors = {}
        if user_input is not None:
            houseId = user_input["houseId"]
            self.home_id = houseId
            session = async_get_clientsession(self.hass)
            xiaoduApi = XiaoDuAPI(self.cookie, session)
            self._device_wifi_id_dict = await xiaoduApi.get_device_wifi_id_dict(houseId)
            return await self.async_step_device()

        return self.async_show_form(
            step_id="home",
            data_schema=vol.Schema({vol.Required("houseId"): vol.In(self._home_id_list)}),
            errors=errors,
        )

    async def async_step_device(self, user_input=None):
        if user_input is not None:
            applianceIds = user_input["device_ids"]
            devices = [{"applianceId": i, "houseId": self.home_id, "cookie": self.cookie} for i in applianceIds]
            home_name = self._home_id_list.get(self.home_id, "Baidu")

            session = async_get_clientsession(self.hass)
            xiaoduApi = XiaoDuAPI(cookie=self.cookie, session=session)
            detail = await xiaoduApi.get_details(self.home_id, applianceIds)
            applianceTypes = detail.get('appliances', [])

            return self.async_create_entry(
                title=f"XiaoDu：{home_name}",
                data={"devices": devices, "applianceTypes": applianceTypes}
            )

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({
                vol.Required("device_ids"): cv.multi_select(self._device_wifi_id_dict)
            }),
        )

class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: ConfigEntry):
        """Initialize options flow."""
        # config_entry is now automatically available as a read-only property
        # through self.config_entry, which usually returns self._config_entry.
        super().__init__()
        self._config_entry = config_entry
        self._device_wifi_id_dict = {}

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["user", "device_select"]
        )

    async def async_step_user(self, user_input=None):
        errors = {}
        devices = self.config_entry.data.get("devices", [])
        current_cookie = devices[0].get("cookie") if devices else ""

        if user_input is not None:
            new_cookie = user_input[CONF_BAIDUID_COOKIE]
            session = async_get_clientsession(self.hass)
            xiaoduApi = XiaoDuAPI(new_cookie, session)
            try:
                loginFlag = await xiaoduApi.checkSession()
                if not loginFlag[0]:
                    errors["base"] = "invalid_auth"
                else:
                    new_data = {**self.config_entry.data}
                    for device in new_data.get("devices", []):
                        device["cookie"] = new_cookie

                    self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                    return self.async_create_entry(title="", data={})
            except Exception as e:
                _LOGGER.error("Error updating cookie: %s", e)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_BAIDUID_COOKIE, default=current_cookie): str
            }),
            description_placeholders={"BAIDU_COOKIE_hint": "app login BAIDU_COOKIE"},
            errors=errors,
        )

    async def async_step_device_select(self, user_input=None):
        errors = {}
        devices = self.config_entry.data.get("devices", [])
        if not devices:
            return self.async_abort(reason="no_devices_found")

        cookie = devices[0].get("cookie")
        house_id = devices[0].get("houseId")

        if user_input is not None:
            applianceIds = user_input["device_ids"]
            new_devices = [{"applianceId": i, "houseId": house_id, "cookie": cookie} for i in applianceIds]
            
            session = async_get_clientsession(self.hass)
            xiaoduApi = XiaoDuAPI(cookie=cookie, session=session)
            detail = await xiaoduApi.get_details(house_id, applianceIds)
            applianceTypes = detail.get('appliances', [])

            new_data = {**self.config_entry.data, "devices": new_devices, "applianceTypes": applianceTypes}
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(title="", data={})

        session = async_get_clientsession(self.hass)
        xiaoduApi = XiaoDuAPI(cookie, session)
        self._device_wifi_id_dict = await xiaoduApi.get_device_wifi_id_dict(house_id)
        
        current_ids = [d["applianceId"] for d in devices]

        return self.async_show_form(
            step_id="device_select",
            data_schema=vol.Schema({
                vol.Required("device_ids", default=current_ids): cv.multi_select(self._device_wifi_id_dict)
            }),
            errors=errors,
        )