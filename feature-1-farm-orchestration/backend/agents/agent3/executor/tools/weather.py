import requests


def get_weather():
    url = "https://api.open-meteo.com/v1/forecast?latitude=28.6&longitude=77.2&current_weather=true"

    res = requests.get(url).json()

    weather = res["current_weather"]

    return {
        "air_temp": weather["temperature"],
    }
