"""
integrations/weather.py — Open-Meteo 날씨 조회
"""
import requests


def get_weather(city: str = "부산"):
    """Open-Meteo 무료 API로 날씨 조회 (로그인 불필요)"""
    CITY_COORDS = {
        "부산": (35.1796, 129.0756),
        "서울": (37.5665, 126.9780),
        "대구": (35.8714, 128.6014),
        "인천": (37.4563, 126.7052),
        "광주": (35.1595, 126.8526),
        "대전": (36.3504, 127.3845),
        "울산": (35.5384, 129.3114),
    }
    city_lower = city.strip()
    coords = CITY_COORDS.get(city_lower, CITY_COORDS["부산"])
    lat, lon = coords
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            f"&daily=temperature_2m_max,temperature_2m_min,weather_code"
            f"&timezone=Asia%2FSeoul&forecast_days=3"
        )
        resp = None
        for _attempt in range(3):
            try:
                resp = requests.get(url, timeout=8)
                resp.raise_for_status()
                break
            except Exception:
                if _attempt == 2:
                    raise
        if resp is None:
            raise RuntimeError("날씨 요청 실패")
        data = resp.json()
        cur = data["current"]
        daily = data["daily"]

        WMO_CODES = {
            0: "☀️ 맑음", 1: "🌤️ 대체로 맑음", 2: "⛅ 구름 조금", 3: "☁️ 흐림",
            45: "🌫️ 안개", 48: "🌫️ 짙은 안개",
            51: "🌦️ 이슬비", 53: "🌦️ 이슬비", 55: "🌦️ 강한 이슬비",
            61: "🌧️ 비", 63: "🌧️ 비", 65: "🌧️ 강한 비",
            71: "❄️ 눈", 73: "❄️ 눈", 75: "❄️ 강한 눈",
            80: "🌦️ 소나기", 81: "🌦️ 소나기", 82: "⛈️ 강한 소나기",
            95: "⛈️ 뇌우", 96: "⛈️ 우박 동반 뇌우",
        }
        weather_desc = WMO_CODES.get(cur["weather_code"], f"코드 {cur['weather_code']}")

        lines = [
            f"🌡️ **{city_lower} 현재 날씨**",
            f"{weather_desc}  {cur['temperature_2m']}°C",
            f"습도 {cur['relative_humidity_2m']}%  바람 {cur['wind_speed_10m']}km/h",
            "",
            "**3일 예보**",
        ]
        for i, date in enumerate(daily["time"][:3]):
            day = "오늘" if i == 0 else ("내일" if i == 1 else "모레")
            desc = WMO_CODES.get(daily["weather_code"][i], "")
            hi = daily["temperature_2m_max"][i]
            lo = daily["temperature_2m_min"][i]
            lines.append(f"{day} {desc}  최고 {hi}° / 최저 {lo}°")
        return "\n".join(lines)
    except Exception as e:
        return f"😥 날씨 조회 오류: {e}"
