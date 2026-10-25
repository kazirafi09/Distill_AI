"""Version-pinned suite and assessment counts; historical runs keep their scope."""
REPETITIONS = 1
PERSONAS = ("strong_all_rounder", "keyword_stuffer")
LEGACY_PERSONAS = (*PERSONAS, "near_miss")


def personas_for(versions):
    config = versions.get("generation", {})
    keys = config.get("persona_keys", LEGACY_PERSONAS)
    if not isinstance(keys, (list, tuple)) or not all(isinstance(key, str) for key in keys):
        raise ValueError("SYNTHETIC_PERSONAS_INVALID")
    personas = next((known for known in (PERSONAS, LEGACY_PERSONAS)
                     if len(keys) == len(known) and set(keys) == set(known)), None)
    count = config.get("persona_count", len(keys))
    if personas is None or type(count) is not int or count != len(personas):
        raise ValueError("SYNTHETIC_PERSONAS_INVALID")
    return personas


def repetitions_for(versions):
    count = versions.get("assessment_repetitions", 3)
    if type(count) is not int or count not in {1, 3}:
        raise ValueError("SYNTHETIC_REPETITIONS_INVALID")
    return count
