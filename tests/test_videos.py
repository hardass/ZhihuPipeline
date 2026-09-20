from zhihu_pipeline.videos import (
    extract_pin_detail,
    extract_pin_video_assets,
    render_video_embeds,
)


def sample_pin():
    return {
        "title": "fallback title",
        "raw_content": {
            "author": {"name": "作者"},
            "created": 1787000000,
            "like_count": 7,
            "content": [
                {
                    "type": "text",
                    "title": "正确标题",
                    "content": "<p>动作说明。</p>",
                },
                {
                    "type": "video",
                    "video_id": "video-123",
                    "video_info": {
                        "duration": 10,
                        "playlist": {
                            "ld": {
                                "url": "https://vdn3.vzuu.com/SD/video.mp4",
                                "format": "mp4",
                                "size": 1234,
                                "width": 478,
                                "height": 848,
                            },
                            "hd": {
                                "url": "https://vdn3.vzuu.com/HD/video.mp4",
                                "format": "mp4",
                                "size": 4567,
                                "width": 720,
                                "height": 1280,
                            },
                        },
                    },
                },
            ],
        },
    }


def test_extract_pin_detail_uses_full_text_title():
    detail = extract_pin_detail(sample_pin())

    assert detail["title"] == "正确标题"
    assert detail["content_html"] == "<p>动作说明。</p>"
    assert detail["author_name"] == "作者"
    assert detail["vote_count"] == 7


def test_extract_pin_video_assets_defaults_to_lowest_quality():
    assets = extract_pin_video_assets(sample_pin()["raw_content"])

    assert len(assets) == 1
    assert assets[0].quality == "ld"
    assert assets[0].size == 1234
    assert assets[0].width == 478
    assert assets[0].height == 848


def test_extract_pin_video_assets_can_select_hd():
    assets = extract_pin_video_assets(sample_pin()["raw_content"], "hd")

    assert assets[0].quality == "hd"
    assert assets[0].size == 4567


def test_render_video_embeds():
    assets = extract_pin_video_assets(sample_pin()["raw_content"])
    rendered = render_video_embeds([{"asset": assets[0], "vault_path": "assets/知乎视频/note/video-123-ld.mp4"}])

    assert rendered == "## 视频\n\n![[assets/知乎视频/note/video-123-ld.mp4]]"
