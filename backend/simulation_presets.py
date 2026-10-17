"""Offline simulation presets, not real devices or network locations.

New city entries: GeoNames cities15000, downloaded 2026-10-17, CC BY 4.0.
https://download.geonames.org/export/dump/ ; https://www.geonames.org/
Lat/lon/timezone extracted by GeoNames id, Russian labels added locally.
The original three coordinates are retained for compatibility with 0.7.10 fixtures.
Device parameters come from the installed Playwright device descriptors.
"""

CITIES = {
    'tver': {'name': 'Тверь', 'latitude': 56.8587, 'longitude': 35.9176, 'timezone': 'Europe/Moscow'},
    'moscow': {'name': 'Москва', 'latitude': 55.7558, 'longitude': 37.6173, 'timezone': 'Europe/Moscow'},
    'spb': {'name': 'Санкт-Петербург', 'latitude': 59.9343, 'longitude': 30.3351, 'timezone': 'Europe/Moscow'},
    'kazan': {'name': 'Казань', 'latitude': 55.78874, 'longitude': 49.12214, 'timezone': 'Europe/Moscow', 'geoname_id': 551487},
    'novosibirsk': {'name': 'Новосибирск', 'latitude': 55.02259, 'longitude': 82.93175, 'timezone': 'Asia/Novosibirsk', 'geoname_id': 1496747},
    'yekaterinburg': {'name': 'Екатеринбург', 'latitude': 56.85733, 'longitude': 60.61529, 'timezone': 'Asia/Yekaterinburg', 'geoname_id': 1486209},
    'nizhny-novgorod': {'name': 'Нижний Новгород', 'latitude': 56.32867, 'longitude': 44.00205, 'timezone': 'Europe/Moscow', 'geoname_id': 520555},
    'samara': {'name': 'Самара', 'latitude': 53.20767, 'longitude': 50.13553, 'timezone': 'Europe/Samara', 'geoname_id': 499099},
    'omsk': {'name': 'Омск', 'latitude': 54.99244, 'longitude': 73.36859, 'timezone': 'Asia/Omsk', 'geoname_id': 1496153},
    'chelyabinsk': {'name': 'Челябинск', 'latitude': 55.1611, 'longitude': 61.42877, 'timezone': 'Asia/Yekaterinburg', 'geoname_id': 1508291},
    'rostov': {'name': 'Ростов-на-Дону', 'latitude': 47.21997, 'longitude': 39.70769, 'timezone': 'Europe/Moscow', 'geoname_id': 501175},
    'ufa': {'name': 'Уфа', 'latitude': 54.74306, 'longitude': 55.96779, 'timezone': 'Asia/Yekaterinburg', 'geoname_id': 479561},
    'krasnoyarsk': {'name': 'Красноярск', 'latitude': 56.03742, 'longitude': 92.93136, 'timezone': 'Asia/Krasnoyarsk', 'geoname_id': 1502026},
    'perm': {'name': 'Пермь', 'latitude': 58.01046, 'longitude': 56.25017, 'timezone': 'Asia/Yekaterinburg', 'geoname_id': 511196},
    'voronezh': {'name': 'Воронеж', 'latitude': 51.66833, 'longitude': 39.19204, 'timezone': 'Europe/Moscow', 'geoname_id': 472045},
    'volgograd': {'name': 'Волгоград', 'latitude': 48.71378, 'longitude': 44.4976, 'timezone': 'Europe/Volgograd', 'geoname_id': 472757},
    'krasnodar': {'name': 'Краснодар', 'latitude': 45.04534, 'longitude': 38.98178, 'timezone': 'Europe/Moscow', 'geoname_id': 542420},
    'saratov': {'name': 'Саратов', 'latitude': 51.54048, 'longitude': 45.9901, 'timezone': 'Europe/Saratov', 'geoname_id': 498677},
    'tyumen': {'name': 'Тюмень', 'latitude': 57.15222, 'longitude': 65.52722, 'timezone': 'Asia/Yekaterinburg', 'geoname_id': 1488754},
    'vladivostok': {'name': 'Владивосток', 'latitude': 43.10562, 'longitude': 131.87353, 'timezone': 'Asia/Vladivostok', 'geoname_id': 2013348},
    'irkutsk': {'name': 'Иркутск', 'latitude': 52.29566, 'longitude': 104.29076, 'timezone': 'Asia/Irkutsk', 'geoname_id': 2023469},
    'khabarovsk': {'name': 'Хабаровск', 'latitude': 48.46204, 'longitude': 135.0971, 'timezone': 'Asia/Vladivostok', 'geoname_id': 2022890},
    'kaliningrad': {'name': 'Калининград', 'latitude': 54.70639, 'longitude': 20.51102, 'timezone': 'Europe/Kaliningrad', 'geoname_id': 554234},
    'sochi': {'name': 'Сочи', 'latitude': 43.59699, 'longitude': 39.72477, 'timezone': 'Europe/Moscow', 'geoname_id': 491422},
    'tomsk': {'name': 'Томск', 'latitude': 56.50049, 'longitude': 84.98216, 'timezone': 'Asia/Tomsk', 'geoname_id': 1489425},
}

DEVICES = {
    'android': {'name': 'Android · Pixel 7', 'preset': 'Pixel 7'},
    'windows': {'name': 'Windows 10 · Chrome', 'preset': 'Desktop Chrome'},
    'iphone': {'name': 'iPhone 13', 'preset': 'iPhone 13'},
    'pixel5': {'name': 'Android · Pixel 5', 'preset': 'Pixel 5'},
    'pixel8pro': {'name': 'Android · Pixel 8 Pro', 'preset': 'Pixel 8 Pro'},
    'pixel9': {'name': 'Android · Pixel 9', 'preset': 'Pixel 9'},
    'galaxys24': {'name': 'Android · Galaxy S24', 'preset': 'Galaxy S24'},
    'galaxya55': {'name': 'Android · Galaxy A55', 'preset': 'Galaxy A55'},
    'iphone15pro': {'name': 'iPhone 15 Pro', 'preset': 'iPhone 15 Pro'},
    'iphone16promax': {'name': 'iPhone 16 Pro Max', 'preset': 'iPhone 16 Pro Max'},
    'ipadpro11': {'name': 'Планшет · iPad Pro 11', 'preset': 'iPad Pro 11'},
    'galaxytabs9': {'name': 'Планшет · Galaxy Tab S9', 'preset': 'Galaxy Tab S9'},
}
