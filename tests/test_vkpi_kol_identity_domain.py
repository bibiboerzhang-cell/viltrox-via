from app.domains.kol import identity


def test_kol_identity_normalizes_platforms_and_handles():
    assert identity.normalize_platform("Instagram") == "ig"
    assert identity.normalize_platform("twitter") == "x"
    assert identity.normalize_handle("https://www.youtube.com/@LensCreator?view=1", "youtube") == "lenscreator"
    assert identity.normalize_handle("@Lens.Creator!", "instagram") == "lens.creator"


def test_kol_identity_dedup_key_is_stable_and_email_aware():
    first = identity.dedup_key("youtube", "https://youtube.com/@LensCreator", "A@EXAMPLE.COM")
    second = identity.dedup_key("yt", "@lenscreator", "a@example.com")
    third = identity.dedup_key("yt", "@lenscreator", "")

    assert first == second
    assert first != third
