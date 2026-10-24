"""Busqueda en TMDB (la base que usa Jellyfin) para proponer el año de una
pelicula. Se elige el resultado cuya duracion se parece a la del archivo, asi
'Papillon' de 150 min es la de 1973 y no la de 2017 (133 min).

Requiere una clave de API gratuita de themoviedb.org (v3 o token v4).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

API = "https://api.themoviedb.org/3"


class TmdbError(Exception):
    pass


@dataclass
class Candidate:
    title: str
    original_title: str
    year: Optional[int]
    runtime: Optional[int]
    tmdb_id: int

    def label(self) -> str:
        rt = f", {self.runtime} min" if self.runtime else ""
        return f"{self.title} ({self.year or '?'}){rt}"


class Tmdb:
    def __init__(self, key: str, language: str = "es-ES", timeout: float = 10.0):
        self.key = key.strip()
        self.language = language
        self.timeout = timeout

    def _get(self, path: str, **params) -> dict:
        headers = {"Accept": "application/json"}
        if len(self.key) > 40:  # token de lectura v4
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            params["api_key"] = self.key
        params.setdefault("language", self.language)
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return json.load(res)
        except Exception as exc:  # red, 401, JSON roto: todo es "no se pudo"
            raise TmdbError(str(exc)) from exc

    def search_movie(self, title: str, limit: int = 5) -> list[Candidate]:
        data = self._get("/search/movie", query=title, include_adult="false")
        out = []
        for r in data.get("results", [])[:limit]:
            date = r.get("release_date") or ""
            details = self._get(f"/movie/{r['id']}")
            out.append(Candidate(r.get("title", ""), r.get("original_title", ""),
                                 int(date[:4]) if date[:4].isdigit() else None,
                                 details.get("runtime") or None, r["id"]))
        return out


    def tv_seasons(self, name: str, year: Optional[int] = None) -> Optional[dict]:
        """Episodios por temporada de la serie que mejor coincide, o None."""
        params = {"query": name}
        if year:
            params["first_air_date_year"] = year
        results = self._get("/search/tv", **params).get("results", [])
        if not results and year:
            results = self._get("/search/tv", query=name).get("results", [])
        if not results:
            return None
        r = results[0]
        details = self._get(f"/tv/{r['id']}")
        date = r.get("first_air_date") or ""
        return {"id": r["id"], "name": r.get("name", ""),
                "year": int(date[:4]) if date[:4].isdigit() else None,
                "seasons": {s["season_number"]: s.get("episode_count") or 0
                            for s in details.get("seasons", [])}}


def best_match(cands: list[Candidate], duration_min: int,
               tolerance: int = 6) -> tuple[Optional[Candidate], str]:
    """El candidato con duracion mas cercana dentro de la tolerancia (minutos).
    Si no hay duracion conocida, solo se acepta si hay un unico resultado."""
    if not cands:
        return None, "sin resultados en TMDB"
    if not duration_min:
        if len(cands) == 1:
            return cands[0], "unico resultado (sin duracion para comprobar)"
        return None, "varios resultados y sin duracion para elegir"
    timed = [c for c in cands if c.runtime]
    if not timed:
        return None, "TMDB no da duraciones"
    best = min(timed, key=lambda c: abs(c.runtime - duration_min))
    diff = abs(best.runtime - duration_min)
    if diff > tolerance:
        return None, f"ninguna duracion coincide (la mas cercana difiere {diff} min)"
    rivals = [c for c in timed if c is not best and abs(c.runtime - duration_min) <= tolerance
              and c.year != best.year]
    if rivals:
        return None, "varias versiones con duracion parecida: elegir a mano"
    return best, f"duracion {duration_min} min ≈ {best.runtime} min"
