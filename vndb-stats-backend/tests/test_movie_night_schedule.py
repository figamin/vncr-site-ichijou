"""Tests for Movie Night's weekly schedule + vote ping.

The cog reads its weekly day/time on the club's clock (America/New_York by default),
so "Saturday 12:00" has to mean noon Eastern and track EST/EDT across the year. Get
that wrong and the showtime ping lands an hour out twice a year, or on the wrong day
for an evening slot. The vote ping must also fire once per round: a repost, a restart
or a scheduler tick that reposts the board must not ping the role again.

Everything here is pure: no database, no Discord (a stand-in channel object is enough).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from discord_bot.cogs.movie_night import MovieNightCog

SATURDAY = 5


def cog(**attrs) -> MovieNightCog:
    c = MovieNightCog(bot=None)  # __init__ only stores the bot
    c._show_weekday = SATURDAY
    c._show_time = "12:00"
    for key, value in attrs.items():
        setattr(c, key, value)
    return c


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_saturday_noon_et_follows_est_and_edt():
    # Saturday 31 Oct 2026, 08:00 UTC (04:00 EDT) -> today 12:00 EDT == 16:00 UTC.
    assert cog()._next_default_showtime(utc(2026, 10, 31, 8, 0)) == utc(2026, 10, 31, 16, 0)
    # A week later the US is back on standard time: 12:00 EST == 17:00 UTC.
    assert cog()._next_default_showtime(utc(2026, 11, 7, 8, 0)) == utc(2026, 11, 7, 17, 0)


def test_next_showtime_rolls_forward_a_week_once_passed():
    # Saturday 15:00 UTC is 11:00 EDT, so today's noon is still ahead...
    assert cog()._next_default_showtime(utc(2026, 10, 31, 15, 0)) == utc(2026, 10, 31, 16, 0)
    # ...but at 17:00 UTC (13:00 EDT) it has passed, so it moves to the next Saturday.
    assert cog()._next_default_showtime(utc(2026, 10, 31, 17, 0)) == utc(2026, 11, 7, 17, 0)


def test_weekday_is_matched_on_the_club_clock_not_utc():
    # Sunday 01:00 UTC is Saturday 21:00 in New York, so a 22:00 Saturday slot is still
    # "today" for the club (01:00 UTC on the Sunday) and must not jump a week.
    c = cog(_show_time="22:00")
    assert c._next_default_showtime(utc(2026, 11, 1, 1, 0)) == utc(2026, 11, 1, 2, 0)


def test_timezone_is_configurable():
    c = cog(_timezone="Europe/London", _show_time="12:00")
    # Saturday 19 Sep 2026 12:00 BST (UTC+1).
    assert c._next_default_showtime(utc(2026, 9, 19, 6, 0)) == utc(2026, 9, 19, 11, 0)


def test_bad_stored_timezone_falls_back_to_the_default():
    assert cog(_timezone="Mars/Olympus_Mons").tz().key == "America/New_York"


def test_schedule_label_names_the_zone_and_abbreviation():
    assert cog().schedule_label(utc(2026, 11, 7, 8, 0)) == "Saturday at 12:00 EST (America/New_York)"
    assert cog().schedule_label(utc(2026, 10, 31, 8, 0)) == "Saturday at 12:00 EDT (America/New_York)"
    assert cog(_show_time="").schedule_label() == "Not set"


def test_vote_ping_fires_once_per_round():
    c = cog(_notify_role_id=999)
    channel = SimpleNamespace(guild=SimpleNamespace(id=1))
    cycle = SimpleNamespace(id=3, scheduled_for=utc(2026, 11, 7, 17, 0))

    content, mentions = c._vote_ping(cycle, channel)
    assert "<@&999>" in content
    assert [r.id for r in mentions.roles] == [999]

    # Same round after it fired: a repost (or a restart) must stay silent.
    c._vote_pinged_for = c._round_stamp(cycle)
    assert c._vote_ping(cycle, channel) is None

    # Next week's round pings again.
    cycle.scheduled_for = utc(2026, 11, 14, 17, 0)
    assert c._vote_ping(cycle, channel) is not None


def test_vote_ping_is_off_without_a_role():
    c = cog(_notify_role_id=None)
    cycle = SimpleNamespace(id=3, scheduled_for=utc(2026, 11, 7, 17, 0))
    assert c._vote_ping(cycle, SimpleNamespace(guild=None)) is None


def test_ping_mentions_handles_the_everyone_role():
    c = cog(_notify_role_id=42)
    guild = SimpleNamespace(id=42)
    mentions = c._ping_mentions(SimpleNamespace(guild=guild))
    assert mentions.everyone is True and mentions.roles is False
