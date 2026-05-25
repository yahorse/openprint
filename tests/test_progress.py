from openprint.progress import JobProgressTracker


def test_tracker_init():
    tracker = JobProgressTracker(poll_interval=5.0)
    assert tracker._interval == 5.0
    assert tracker._tracked == {}


def test_parse_lpstat_active():
    output = (
        "HP_LaserJet-42      user  1024  Mon 01 Jan 2026 10:00:00\n"
        "Canon-99            user  2048  Mon 01 Jan 2026 10:01:00\n"
    )
    jobs = JobProgressTracker._parse_lpstat(output)
    assert 42 in jobs
    assert 99 in jobs
    assert jobs[42] == "active"


def test_parse_lpstat_held():
    output = "HP_LaserJet-5       user  1024  held since Mon 01 Jan\n"
    jobs = JobProgressTracker._parse_lpstat(output)
    assert jobs[5] == "held"


def test_parse_lpstat_empty():
    jobs = JobProgressTracker._parse_lpstat("")
    assert jobs == {}
