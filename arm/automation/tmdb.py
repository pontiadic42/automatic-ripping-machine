"""
The few TMDb lookups automation needs: runtimes, a search with runtimes, TV seasons.
All calls time out and return None on any problem, so a TMDb outage never stops a rip.
"""
import functools
import logging
import re

import requests

import arm.config.config as cfg

API = "https://api.themoviedb.org/3"
POSTER = "https://image.tmdb.org/t/p/w342"


def _key():
    return str(cfg.arm_config.get("TMDB_API_KEY") or "")


def _get(path, **params):
    if not _key():
        return None
    params["api_key"] = _key()
    try:
        response = requests.get(f"{API}{path}", params=params, timeout=15)
        if response.status_code != 200:
            logging.debug(f"TMDb {path}: HTTP {response.status_code}")
            return None
        return response.json()
    except (requests.RequestException, ValueError) as error:
        logging.info(f"TMDb lookup failed ({path}): {error}")
        return None


@functools.lru_cache(maxsize=64)
def find_by_imdb(imdb_id):
    """(media_type, tmdb_id) for an IMDb id, or None"""
    if not imdb_id or not str(imdb_id).startswith("tt"):
        return None
    data = _get(f"/find/{imdb_id}", external_source="imdb_id")
    if not data:
        return None
    if data.get("movie_results"):
        return "movie", data["movie_results"][0]["id"]
    if data.get("tv_results"):
        return "tv", data["tv_results"][0]["id"]
    return None


@functools.lru_cache(maxsize=128)
def movie_details(tmdb_id):
    return _get(f"/movie/{int(tmdb_id)}", append_to_response="external_ids")


@functools.lru_cache(maxsize=32)
def tv_details(tmdb_id):
    return _get(f"/tv/{int(tmdb_id)}", append_to_response="external_ids")


@functools.lru_cache(maxsize=64)
def season(tmdb_id, season_number):
    return _get(f"/tv/{int(tmdb_id)}/season/{int(season_number)}")


def runtime_minutes(imdb_id):
    """Movie runtime, or typical episode runtime for a series, from TMDb"""
    found = find_by_imdb(imdb_id)
    if not found:
        return None
    kind, tmdb_id = found
    if kind == "movie":
        details = movie_details(tmdb_id) or {}
        return details.get("runtime") or None
    details = tv_details(tmdb_id) or {}
    runtimes = details.get("episode_run_time") or []
    return runtimes[0] if runtimes else None


def search_movies(query, limit=8):
    """Search results with their runtimes (one extra call per result)"""
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    if len(query) < 2:
        return []
    data = _get("/search/movie", query=query) or {}
    results = []
    for item in (data.get("results") or [])[:limit]:
        details = movie_details(item["id"]) or {}
        results.append({
            "tmdb_id": item["id"],
            "title": item.get("title") or "",
            "year": (item.get("release_date") or "")[:4],
            "runtime": details.get("runtime") or None,
            "imdb_id": (details.get("external_ids") or {}).get("imdb_id") or details.get("imdb_id"),
            "poster_url": f"{POSTER}{item['poster_path']}" if item.get("poster_path") else None,
            "popularity": item.get("popularity") or 0,
        })
    return results
