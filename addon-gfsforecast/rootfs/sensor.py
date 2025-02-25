import os
import json
from requests import get, post
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import utils
import time
from logger import logger
from typing import Any

class Sensor:
    _api_token: str
    _api_url: str = 'http://supervisor/core/api'
    _sensor_data: dict[str, Any] = {}
    _sensor_file_path: str
    _latitude: float
    _longitude: float
    _time_zone: str
    _metric_system: bool

    def __init__(self, api_token: str, entity_id: str, unit_system: str) -> None:
        self._api_token = api_token
        self._entity_id = entity_id
        self._sensor_file_path: str = f'/data/{entity_id}.json'
        self._metric_system = unit_system == 'Metric'
        self._get_HA_config()

    def __get_api_url(self) -> str:
        return f"{self._api_url}/states/{self._entity_id}"

    def __get_api_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json"
        }
        return headers

    def __send_data_to_ha(self) -> None:
        url = self.__get_api_url()
        headers = self.__get_api_headers()
        response = post(url, json=self._sensor_data, headers=headers)
        if not (response.status_code in [200, 201]):
            logger.error(f'Error __send_data_to_ha: {response.status_code} - {response.text}')
            return
        self.__store_sensor_data()

    def restore_ha_sensor(self) -> None:
        self.__restore_sensor_data()
        self.__send_data_to_ha()

    def __restore_sensor_data(self) -> None:
        if os.path.exists(self._sensor_file_path):
            file = open(self._sensor_file_path,)
            storedData = json.load(file)
            file.close()
            self._sensor_data = storedData
        else:
            self._sensor_data = {
                "state": "",
                "name": "GFS forecast",
                "attributes": {
                    "icon": "mdi:weather-partly-cloudy",
                    "friendly_name": "GFS forecast",
                    "current": {},
                    "forecast": [],
                    "loading": {}
                }
            }

    def __store_sensor_data(self):
        file = open(self._sensor_file_path, 'w')
        json.dump(self._sensor_data, file)
        file.close()

    def set_sensor_base_data(self, gfs_date: date, gfs_pass: int):
        self.__restore_sensor_data()

        if not self._sensor_data['attributes']['current']:
            self._sensor_data['attributes']['current'] = {
                'date': 'unknown',
                'pass': 'unknown'
            }
        else:
            current: dict[str, Any] = self._sensor_data['attributes']['current']
            if ('date' in current) & ('pass' in current):
                if (current['date'] != gfs_date.isoformat()) | (current['pass'] != gfs_pass):
                    self._sensor_data['state'] = "Initializing"
                    self._sensor_data['attributes']['loading'] = {
                        'date': gfs_date.isoformat(),
                        'pass': gfs_pass
                    }
        self.__send_data_to_ha()

    def update_sensor_during_loading(self, gfs_date: date, gfs_pass: int, offset: int) -> None:
        self.__restore_sensor_data()
        self._sensor_data['state'] = "Loading"
        self._sensor_data['attributes']['loading'] = {
            'date': gfs_date.isoformat(),
            'pass': gfs_pass,
            'offset': offset
        }
        self.__send_data_to_ha()

    def update_sensor_with_full_data(self, gfs_data: dict[str, Any], day_forecast: dict[date, Any], detailed: bool) -> None:
        self.__restore_sensor_data()
        self._sensor_data['state'] = "Finished"
        self._sensor_data['attributes']['current'] = {
            'date': gfs_data['info']['date'],
            'pass': gfs_data['info']['pass'],
            'used_latitude': gfs_data['info']['used_latitude'],
            'used_longitude': gfs_data['info']['used_longitude']
        }
        if self._metric_system:
            self._sensor_data['attributes']['pressure_unit'] = 'hPa'
            self._sensor_data['attributes']['wind_speed_unit'] = 'm/s'
            self._sensor_data['attributes']['precipitation_unit'] = 'mm'
            self._sensor_data['attributes']['temperature_unit'] = '°C'
        else:
            self._sensor_data['attributes']['pressure_unit'] = 'inHg'
            self._sensor_data['attributes']['wind_speed_unit'] = 'mph'
            self._sensor_data['attributes']['precipitation_unit'] = 'inch'
            self._sensor_data['attributes']['temperature_unit'] = '°F'
        self._sensor_data['attributes']['loading'] = {}
        self._sensor_data['attributes']['forecast'] = []
        self._sensor_data['attributes']['last_updated'] = datetime.now(tz=ZoneInfo(self._time_zone)).isoformat()
        if detailed:
            self._sensor_data['attributes']['detailed_forecast'] = []
            for offset in gfs_data:
                if offset == 'info':
                    continue
                data = gfs_data[offset]
                dt = datetime.fromisoformat(gfs_data['info']['date'])
                dt = datetime(dt.year, dt.month, dt.day, gfs_data['info']['pass'], 0, 0, 0, tzinfo=ZoneInfo(self._time_zone))
                dt += timedelta(hours = int(offset))
                detailed_forecast: dict[str, Any] = {
                    "datetime": dt.isoformat()
                }

                windspeed, windangle = utils.get_wind_info(data['vwind'], data['uwind'], self._metric_system)
                detailed_forecast['windspeed'] = windspeed
                if self._metric_system:
                    detailed_forecast['windspeed_bft'] = utils.convert_ms_to_bft(windspeed)
                detailed_forecast['windangle'] = windangle
                detailed_forecast['windrose'] = utils.get_wind_rose(windangle)
                detailed_forecast['gust'] = utils.ms_to_kmh(data['gust'])
                detailed_forecast['temperature'] = data['tmp']
                detailed_forecast['rain'] = data['rain']
                detailed_forecast['pressure'] = round(data['pres'] / 100, 1)
                detailed_forecast['visibility'] = round(data['vis'])
                detailed_forecast['cldhigh'] = round(data['cldhigh'])
                detailed_forecast['cldmid'] = round(data['cldmid'])
                detailed_forecast['cldlow'] = round(data['cldlow'])
                detailed_forecast['cldtotal'] = round(data['cldtotal'])
                detailed_forecast['tmp500hpa'] = data['tmp500hpa']
                detailed_forecast['cape'] = round(data['cape'])
                detailed_forecast['liftedindex'] = round(data['liftedindex'])
                detailed_forecast['offset'] = int(offset)

                self._sensor_data['attributes']['detailed_forecast'].append(detailed_forecast)

        for gfs_date in day_forecast:
            if gfs_date > datetime.today().date():
                self._sensor_data['attributes']['forecast'].append(self.__get_sensor_forecast(day_forecast[gfs_date], gfs_date))

        self.__send_data_to_ha()

    def __get_sensor_forecast(self, day_forecast: dict[str, Any], gfs_date: date) -> dict[str, Any]:
        sensor_forecast: dict[str, Any] = {}
        dt = datetime.combine(gfs_date, datetime.min.time(), tzinfo=ZoneInfo(self._time_zone))
        sensor_forecast['datetime'] = dt.isoformat()
        sensor_forecast['condition'] = \
            utils.get_condition(day_forecast['chance_of_sun'], day_forecast['rain'], day_forecast['min_temperature_daytime'])
        if day_forecast['temperature_max'] > -999:
            sensor_forecast['temperature'] = round(day_forecast['temperature_max'])
        if day_forecast['temperature_min'] < 999:
            sensor_forecast['templow'] = round(day_forecast['temperature_min'])
        sensor_forecast['precipitation'] = round(day_forecast['rain'])
        sensor_forecast['wind_speed'] = day_forecast['windspeed']
        if self._metric_system:
            sensor_forecast['wind_speed_bft'] = utils.convert_ms_to_bft(day_forecast['windspeed'])
        sensor_forecast['wind_rose'] = utils.get_wind_rose(day_forecast['windangle'])
        sensor_forecast['wind_bearing'] = round(day_forecast['windangle'])
        sensor_forecast['chance_of_sun'] = round(day_forecast['chance_of_sun'])
        sensor_forecast['chance_of_rain'] = round(day_forecast['chance_of_rain'])
        return sensor_forecast

    def get_gps_position(self) -> tuple[float, float]:
        return self._latitude, self._longitude

    def _get_HA_config(self):
        url = f'{self._api_url}/config'
        headers = self.__get_api_headers()
        count = 0
        while count < 3:
            response = get(url, headers=headers)
            if response.status_code in [200, 201]:
                data = json.loads(response.text)
                logger.info(f'Found gps location {data["latitude"]} {data["longitude"]}')
                self._latitude = data["latitude"]
                self._longitude = data["longitude"]
                self._time_zone = data["time_zone"]
                return
            else:
                time.sleep(1)
            count += 1
        raise ValueError('Could not acquire HA config')
