"""Render the GitHub stats card used by the profile README.

Runs in CI (see .github/workflows/stats.yml) and writes two SVGs into assets/.
Uses the GraphQL API when a token is present, and falls back to the public REST
API so the card can be regenerated locally without credentials.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from html import escape
from pathlib import Path

USER = os.environ.get("PROFILE_USER", "manavc-13")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUT_DIR = Path(__file__).resolve().parent.parent / "assets"

GRAPHQL = """
query($login: String!) {
  user(login: $login) {
    followers { totalCount }
    contributionsCollection {
      totalCommitContributions
      restrictedContributionsCount
      contributionCalendar { totalContributions }
    }
    pullRequests { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 12, orderBy: { field: SIZE, direction: DESC }) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

# Fallback colours for languages GitHub reports without one.
PALETTE = ["#2DD4BF", "#38BDF8", "#818CF8", "#F472B6", "#FBBF24", "#34D399"]


def _request(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_graphql() -> dict:
    payload = json.dumps({"query": GRAPHQL, "variables": {"login": USER}}).encode()
    body = _request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": USER,
        },
    )
    if "errors" in body:
        raise RuntimeError(body["errors"])

    user = body["data"]["user"]
    contrib = user["contributionsCollection"]
    repos = user["repositories"]["nodes"]

    languages: dict[str, dict] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            entry = languages.setdefault(name, {"size": 0, "color": edge["node"]["color"]})
            entry["size"] += edge["size"]

    return {
        "contributions": contrib["contributionCalendar"]["totalContributions"],
        "commits": contrib["totalCommitContributions"] + contrib["restrictedContributionsCount"],
        "pull_requests": user["pullRequests"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "repos": user["repositories"]["totalCount"],
        "followers": user["followers"]["totalCount"],
        "languages": languages,
    }


def fetch_rest() -> dict:
    """Unauthenticated fallback. Contribution counts aren't exposed here."""
    headers = {"User-Agent": USER, "Accept": "application/vnd.github+json"}
    profile = _request(f"https://api.github.com/users/{USER}", headers=headers)
    repos = _request(
        f"https://api.github.com/users/{USER}/repos?per_page=100&type=owner", headers=headers
    )
    repos = [r for r in repos if not r["fork"]]

    languages: dict[str, dict] = {}
    for repo in repos:
        try:
            breakdown = _request(repo["languages_url"], headers=headers)
        except urllib.error.HTTPError:
            continue
        for name, size in breakdown.items():
            entry = languages.setdefault(name, {"size": 0, "color": None})
            entry["size"] += size

    return {
        "contributions": None,
        "commits": None,
        "pull_requests": None,
        "stars": sum(r["stargazers_count"] for r in repos),
        "repos": profile["public_repos"],
        "followers": profile["followers"],
        "languages": languages,
    }


def human(value: int | None) -> str:
    if value is None:
        return "—"
    if value >= 10_000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return str(value)


def top_languages(languages: dict[str, dict], limit: int = 6) -> list[dict]:
    ranked = sorted(languages.items(), key=lambda kv: kv[1]["size"], reverse=True)[:limit]
    total = sum(entry["size"] for _, entry in ranked) or 1
    out = []
    for index, (name, entry) in enumerate(ranked):
        out.append(
            {
                "name": name,
                "share": entry["size"] / total,
                "color": entry["color"] or PALETTE[index % len(PALETTE)],
            }
        )
    return out


def render(stats: dict, dark: bool) -> str:
    if dark:
        bg, border, dot = "#0D1117", "#21262D", "#213041"
        value_fill, label_fill, kicker = "#F0F6FC", "#8B949E", "#2DD4BF"
        track, glow = "#161B22", "#38BDF8"
    else:
        bg, border, dot = "#FFFFFF", "#D8DEE4", "#D5DDE6"
        value_fill, label_fill, kicker = "#0F172A", "#57606A", "#0F766E"
        track, glow = "#EAEEF2", "#38BDF8"

    sans = ("ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, "
            "Roboto, Helvetica, Arial, sans-serif")
    mono = "ui-monospace, SFMono-Regular, 'JetBrains Mono', Menlo, Consolas, monospace"

    tiles = [
        (human(stats["contributions"]), "CONTRIBUTIONS"),
        (human(stats["commits"]), "COMMITS"),
        (human(stats["pull_requests"]), "PULL REQUESTS"),
        (human(stats["repos"]), "REPOSITORIES"),
        (human(stats["followers"]), "FOLLOWERS"),
    ]
    # Only worth a tile once there's something to show.
    if stats["stars"]:
        tiles.insert(3, (human(stats["stars"]), "STARS EARNED"))

    parts: list[str] = []
    left, right = 44, 1156
    span = right - left

    # Stat tiles.
    step = span / len(tiles)
    for index, (value, label) in enumerate(tiles):
        x = left + index * step
        parts.append(
            f'<text x="{x:.1f}" y="140" font-family="{sans}" font-size="36" '
            f'font-weight="700" letter-spacing="-1" fill="{value_fill}">{value}</text>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="164" font-family="{mono}" font-size="10.5" '
            f'letter-spacing="1.7" fill="{label_fill}">{label}</text>'
        )

    # Language bar.
    languages = top_languages(stats["languages"])
    bar_y, bar_h = 236, 10
    parts.append(
        f'<rect x="{left}" y="{bar_y}" width="{span}" height="{bar_h}" rx="{bar_h / 2}" fill="{track}"/>'
    )
    parts.append(
        f'<clipPath id="bar"><rect x="{left}" y="{bar_y}" width="{span}" '
        f'height="{bar_h}" rx="{bar_h / 2}"/></clipPath>'
    )
    cursor = float(left)
    segments = []
    for language in languages:
        width = span * language["share"]
        segments.append(
            f'<rect x="{cursor:.2f}" y="{bar_y}" width="{width:.2f}" '
            f'height="{bar_h}" fill="{language["color"]}"/>'
        )
        cursor += width
    parts.append(f'<g clip-path="url(#bar)">{"".join(segments)}</g>')

    # Legend.
    legend_step = span / max(len(languages), 1)
    for index, language in enumerate(languages):
        x = left + index * legend_step
        name = escape(language["name"])
        percent = f'{language["share"] * 100:.1f}%'
        parts.append(f'<circle cx="{x + 4:.1f}" cy="{284}" r="4" fill="{language["color"]}"/>')
        parts.append(
            f'<text x="{x + 16:.1f}" y="288" font-family="{sans}" font-size="13" '
            f'fill="{value_fill}">{name}</text>'
        )
        parts.append(
            f'<text x="{x + 16:.1f}" y="306" font-family="{mono}" font-size="11" '
            f'fill="{label_fill}">{percent}</text>'
        )

    body = "\n    ".join(parts)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="340" viewBox="0 0 1200 340" fill="none" role="img" aria-label="GitHub statistics for {USER}">
  <defs>
    <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#2DD4BF"/>
      <stop offset="50%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#818CF8"/>
    </linearGradient>
    <linearGradient id="fade" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.85"/>
      <stop offset="70%" stop-color="#ffffff" stop-opacity="0.1"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0%" stop-color="{glow}" stop-opacity="0.16"/>
      <stop offset="100%" stop-color="{glow}" stop-opacity="0"/>
    </radialGradient>
    <pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse">
      <circle cx="1.6" cy="1.6" r="1.6" fill="{dot}"/>
    </pattern>
    <mask id="dotmask"><rect width="1200" height="340" fill="url(#fade)"/></mask>
    <clipPath id="card"><rect width="1200" height="340" rx="18"/></clipPath>
  </defs>

  <g clip-path="url(#card)">
    <rect width="1200" height="340" fill="{bg}"/>
    <rect width="1200" height="340" fill="url(#dots)" mask="url(#dotmask)" opacity="0.8"/>
    <ellipse cx="1050" cy="40" rx="340" ry="240" fill="url(#glow)"/>

    <text x="44" y="56" font-family="{mono}" font-size="13" letter-spacing="3.2" fill="{kicker}">GITHUB ACTIVITY</text>
    <rect x="44" y="72" width="72" height="3" rx="1.5" fill="url(#accent)"/>

    {body}

    <text x="44" y="212" font-family="{mono}" font-size="12" letter-spacing="2.6" fill="{label_fill}">TOP LANGUAGES</text>

    <rect x="0.5" y="0.5" width="1199" height="339" rx="18" fill="none" stroke="{border}"/>
  </g>
</svg>
"""


def main() -> int:
    try:
        stats = fetch_graphql() if TOKEN else fetch_rest()
    except Exception as exc:  # noqa: BLE001 - surface the reason in the job log
        print(f"failed to fetch stats: {exc}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "stats-dark.svg").write_text(render(stats, dark=True), encoding="utf-8")
    (OUT_DIR / "stats-light.svg").write_text(render(stats, dark=False), encoding="utf-8")
    print(f"wrote stats cards ({'graphql' if TOKEN else 'rest fallback'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
