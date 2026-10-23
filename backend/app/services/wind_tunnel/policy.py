"""Version-pinned assessment count; historical runs retain their original count."""
REPETITIONS = 1


def repetitions_for(versions):
    count = versions.get("assessment_repetitions", 3)
    if type(count) is not int or count not in {1, 3}:
        raise ValueError("SYNTHETIC_REPETITIONS_INVALID")
    return count
