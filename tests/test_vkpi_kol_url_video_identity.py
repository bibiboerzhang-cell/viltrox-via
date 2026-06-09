from app.domains.kol.url_deep_crawl import classify_url
from app.domains.kol.video_evidence import _video_identity


def test_instagram_shortcode_urls_classify_as_video_with_or_without_username_prefix():
    samples = [
        ("https://www.instagram.com/reel/DYxGltBM_fY/", "DYxGltBM_fY"),
        ("https://www.instagram.com/shtefutsa/reel/DYxGltBM_fY/", "DYxGltBM_fY"),
        ("https://www.instagram.com/p/DYw3UWUCJ_6/", "DYw3UWUCJ_6"),
        ("https://www.instagram.com/jaysoundo/p/DYw3UWUCJ_6/", "DYw3UWUCJ_6"),
        ("https://www.instagram.com/tv/DYtvCode123/", "DYtvCode123"),
        ("https://www.instagram.com/somecreator/tv/DYtvCode123/", "DYtvCode123"),
    ]

    for url, expected_video_id in samples:
        classified = classify_url(url)

        assert classified.platform == "instagram"
        assert classified.url_type == "video"
        assert classified.video_id == expected_video_id


def test_instagram_evidence_identity_dedupes_direct_and_username_prefixed_shortcode_urls():
    equivalent_pairs = [
        (
            "https://www.instagram.com/reel/DYxGltBM_fY/",
            "https://www.instagram.com/shtefutsa/reel/DYxGltBM_fY/",
        ),
        (
            "https://www.instagram.com/p/DYw3UWUCJ_6/",
            "https://www.instagram.com/jaysoundo/p/DYw3UWUCJ_6/",
        ),
        (
            "https://www.instagram.com/tv/DYtvCode123/",
            "https://www.instagram.com/somecreator/tv/DYtvCode123/",
        ),
    ]

    for direct_url, prefixed_url in equivalent_pairs:
        assert _video_identity(direct_url) == _video_identity(prefixed_url)
        assert _video_identity(direct_url)[0] == "instagram"
