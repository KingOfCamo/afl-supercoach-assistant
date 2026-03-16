from __future__ import annotations

"""AFL.com.au news article scraper for early injury detection.

The structured injury list at /matches/injury-list updates weekly.
Breaking injury news appears in articles hours or days earlier.
This scraper fetches the news index page, filters for injury-related
article URLs, then parses each article to extract player injuries.

Individual article pages are server-rendered with full text in <p> tags
and JSON-LD metadata, even though the news listing is a JS SPA.
"""

import logging
import re
from typing import Any, List, Optional

from bs4 import BeautifulSoup
from sqlalchemy import select

from src.models.database import Injury, Player, get_session, init_db
from src.scrapers.base import BaseScraper
from src.utils.teams import normalize_team

logger = logging.getLogger(__name__)

NEWS_URL = "https://www.afl.com.au/news"

# Keywords in URL slugs that indicate injury-related articles
INJURY_SLUG_KEYWORDS = [
    "injury", "injured", "sideline", "surgery", "ruled-out",
    "hamstring", "shoulder", "knee", "acl", "concussion",
    "ankle", "calf", "groin", "hip", "wrist",
    "out-for", "out-of", "misses", "blow", "setback",
]

# Body text keywords for identifying injury types
INJURY_WORDS = [
    "shoulder", "knee", "hamstring", "ACL", "concussion", "ankle",
    "calf", "groin", "hip", "back", "wrist", "quad", "foot",
    "finger", "hand", "rib", "collarbone", "shin", "neck",
    "elbow", "thigh", "adductor", "abductor", "meniscus",
    "achilles", "PCL", "MCL",
]

# Pattern for extracting return timelines from article text
TIMELINE_RE = re.compile(
    r"(\d+[-\u2013]\d+\s+(?:weeks?|months?)"
    r"|\d+\s+(?:weeks?|months?)"
    r"|bulk of (?:the )?season"
    r"|rest of (?:the )?season"
    r"|season[- ]ending"
    r"|indefinite"
    r"|round\s+\d+)",
    re.IGNORECASE,
)

# Pattern to split text into sentences
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _sentences_mentioning(text: str, name: str) -> List[str]:
    """Return sentences from text that contain the player name."""
    sentences = SENTENCE_RE.split(text)
    return [s for s in sentences if name in s]


def _extract_injury_type(sentences: List[str], keywords: List[str]) -> Optional[str]:
    """Find injury type from sentences. Returns formatted string like 'Shoulder (surgery)'."""
    found_type = None
    has_surgery = False

    for sentence in sentences:
        lower = sentence.lower()
        if "surgery" in lower or "surgical" in lower or "operation" in lower:
            has_surgery = True
        for kw in keywords:
            if kw.lower() in lower:
                found_type = kw.capitalize()
                break
        if found_type:
            break

    if found_type and has_surgery:
        return f"{found_type} (surgery)"
    return found_type


def _extract_timeline(sentences: List[str], pattern: re.Pattern) -> Optional[str]:
    """Find first timeline match in sentences."""
    for sentence in sentences:
        match = pattern.search(sentence)
        if match:
            return match.group(0).strip()
    return None


def _infer_status(injury_type: Optional[str], timeline: Optional[str]) -> str:
    """Infer injury status from type and timeline."""
    if injury_type and "surgery" in injury_type.lower():
        return "OUT"
    if timeline:
        tl = timeline.lower()
        if "season" in tl or "indefinite" in tl:
            return "OUT"
        if "test" in tl:
            return "TEST"
    return "OUT"


class AflNewsScraper(BaseScraper):
    """Scrapes AFL.com.au news articles for early injury detection."""

    async def scrape(self, **kwargs: Any) -> int:
        """Scrape injury news from AFL.com.au articles."""
        init_db()
        return await self.scrape_news_injuries()

    async def scrape_news_injuries(self) -> int:
        """Discover injury articles and extract player injury data.

        Returns:
            Number of injury records created or updated.
        """
        # 1. Fetch news index, extract article URLs from raw HTML
        logger.info("Fetching AFL.com.au news index")
        html = await self.fetch(NEWS_URL)

        article_slugs = re.findall(r"/news/(\d+/[a-z0-9-]+)", html)
        unique_slugs = list(dict.fromkeys(article_slugs))
        logger.info("Found %d unique article URLs in news page HTML", len(unique_slugs))

        # 2. Filter for injury-related URLs by slug keywords
        injury_urls = []
        for slug in unique_slugs:
            if any(kw in slug for kw in INJURY_SLUG_KEYWORDS):
                injury_urls.append(f"https://www.afl.com.au/news/{slug}")

        if not injury_urls:
            logger.info("No injury-related articles found in current news page")
            return 0

        logger.info(
            "Found %d injury-related articles to parse: %s",
            len(injury_urls),
            [u.split("/")[-1][:50] for u in injury_urls],
        )

        # 3. Parse each article for injury information
        session = get_session()
        count = 0
        try:
            # Load all active players once for matching
            all_players = (
                session.execute(
                    select(Player).where(Player.is_active.is_(True))
                )
                .scalars()
                .all()
            )

            for url in injury_urls:
                try:
                    article_html = await self.fetch(url)
                    found = self._extract_injuries(article_html, session, all_players)
                    count += found
                    if found:
                        logger.info("Extracted %d injuries from %s", found, url)
                except Exception as e:
                    logger.warning("Failed to parse article %s: %s", url, e)
                    continue

            session.commit()
            logger.info("AFL news scraper: %d injury records created/updated", count)

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return count

    def _extract_injuries(
        self, html: str, session: object, all_players: List[Player]
    ) -> int:
        """Extract injury information from a single article page.

        Matches player names against the database, then looks for injury
        keywords and timelines in sentences mentioning that player.
        """
        soup = BeautifulSoup(html, "html.parser")

        # Get article text from <p> tags
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
        full_text = " ".join(paragraphs)

        if len(full_text) < 100:
            return 0

        count = 0
        for player in all_players:
            # Check if player's full name appears in article text
            if player.name not in full_text:
                continue

            # Find sentences mentioning this player
            sentences = _sentences_mentioning(full_text, player.name)
            if not sentences:
                continue

            # Extract injury details from those sentences
            injury_type = _extract_injury_type(sentences, INJURY_WORDS)
            timeline = _extract_timeline(sentences, TIMELINE_RE)

            if not injury_type:
                continue  # Player mentioned but no injury detected

            status = _infer_status(injury_type, timeline)

            # Upsert injury record
            existing = session.execute(  # type: ignore[union-attr]
                select(Injury).where(Injury.player_id == player.id).limit(1)
            ).scalar_one_or_none()

            if existing:
                # Only update if article provides more specific info
                if timeline and (
                    not existing.estimated_return
                    or existing.estimated_return in ("TBC", "TBD")
                ):
                    existing.injury_type = injury_type
                    existing.estimated_return = timeline
                    existing.status = status
                    count += 1
                    logger.info(
                        "Updated %s injury: %s, return: %s",
                        player.name, injury_type, timeline,
                    )
            else:
                session.add(  # type: ignore[union-attr]
                    Injury(
                        player_id=player.id,
                        injury_type=injury_type,
                        estimated_return=timeline or "TBC",
                        status=status,
                    )
                )
                count += 1
                logger.info(
                    "New injury from news: %s — %s, return: %s",
                    player.name, injury_type, timeline or "TBC",
                )

        return count
