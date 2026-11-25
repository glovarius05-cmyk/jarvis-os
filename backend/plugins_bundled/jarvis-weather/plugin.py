"""Bundled plugin: jarvis-weather - live weather and forecast via Open-Meteo."""

from __future__ import annotations

import httpx


def register(ctx) -> None:
    async def jarvis_weather(args: dict, _brain: str) -> str:
        location = str(args.get("location") or args.get("city") or "").strip()
        days = max(1, min(int(args.get("days") or 3), 7))
        if not location:
            return "ERROR: 'location' (city name) is required."

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                geo = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": location, "count": 1, "language": "ru", "format": "json"},
                )
                if geo.status_code != 200:
                    return f"ERROR: Geocoding API returned HTTP {geo.status_code}."
                results = geo.json().get("results") or []
                if not results:
                    return f"Location '{location}' not found."
                place = results[0]
                lat, lon = place["latitude"], place["longitude"]
                city_label = f"{place.get('name', location)}, {place.get('country', '')}".strip(", ")

                wx = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
                        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                        "forecast_days": days,
                        "timezone": "auto",
                    },
                )
                if wx.status_code != 200:
                    return f"ERROR: Weather API returned HTTP {wx.status_code}."
                wdata = wx.json()

            cur = wdata.get("current", {})
            daily = wdata.get("daily", {})
            lines = [
                f"Погода в **{city_label}**:",
                f"- Сейчас: {cur.get('temperature_2m')}°C (ощущается как {cur.get('apparent_temperature')}°C), "
                f"влажность {cur.get('relative_humidity_2m')}%, ветер {cur.get('wind_speed_10m')} км/ч",
            ]
            dates = daily.get("time") or []
            tmax = daily.get("temperature_2m_max") or []
            tmin = daily.get("temperature_2m_min") or []
            precip = daily.get("precipitation_probability_max") or []
            if dates:
                lines.append("\nПрогноз:")
                for i, d in enumerate(dates):
                    hi = tmax[i] if i < len(tmax) else "?"
                    lo = tmin[i] if i < len(tmin) else "?"
                    pr = precip[i] if i < len(precip) else "?"
                    lines.append(f"- {d}: от {lo}°C до {hi}°C (осадки {pr}%)")
            return "\n".join(lines)
        except Exception as exc:
            return f"ERROR: Failed to fetch weather: {exc}"

    ctx.register_tool(
        name="jarvis_weather",
        description="Get live weather and 1-7 day forecast for any city (free Open-Meteo API, no key required).",
        handler=jarvis_weather,
        min_mode="readonly",
        schema={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name (e.g. Moscow, Warsaw, London)"},
                "days": {"type": "integer", "description": "Forecast days (1-7, default 3)", "default": 3},
            },
            "required": ["location"],
        },
    )
