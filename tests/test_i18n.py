"""Translation layer: source-as-key lookup, fallback and placeholder format."""

from core import i18n


def test_tr_returns_source_chinese_by_default():
    i18n.set_language(i18n.LANG_ZH)
    assert i18n.tr("打开设置") == "打开设置"


def test_tr_returns_english_after_switch():
    i18n.set_language(i18n.LANG_EN)
    assert i18n.tr("打开设置") == "Open settings"


def test_tr_falls_back_to_chinese_for_untranslated_text():
    i18n.set_language(i18n.LANG_EN)
    # A source string with no English entry returns the source unchanged.
    assert i18n.tr("某个还没翻译的文案") == "某个还没翻译的文案"


def test_tr_formats_placeholders():
    i18n.set_language(i18n.LANG_EN)
    assert i18n.tr("发现新版本 {latest}，点击查看下载", latest="v2.0.0") == (
        "New version v2.0.0 available — click to view"
    )


def test_tr_leaves_missing_placeholder_untouched():
    i18n.set_language(i18n.LANG_ZH)
    assert i18n.tr("发现新版本 {latest}，点击查看下载") == "发现新版本 {latest}，点击查看下载"


def test_resolve_language_accepts_explicit_codes():
    assert i18n.resolve_language("zh") == "zh"
    assert i18n.resolve_language("en") == "en"


def test_resolve_language_auto_is_a_valid_code():
    assert i18n.resolve_language("auto") in (i18n.LANG_ZH, i18n.LANG_EN)
    assert i18n.resolve_language(None) in (i18n.LANG_ZH, i18n.LANG_EN)


def test_resolve_language_unknown_falls_back_to_detected():
    assert i18n.resolve_language("fr") in (i18n.LANG_ZH, i18n.LANG_EN)


def test_set_language_ignores_unknown_code():
    i18n.set_language("de")
    assert i18n.get_language() == i18n.DEFAULT_LANGUAGE


def test_set_language_notifies_listeners_only_on_change():
    calls = []
    i18n.set_language(i18n.LANG_ZH)
    i18n.add_language_listener(lambda: calls.append(1))
    i18n.set_language(i18n.LANG_ZH)  # same language → no notify
    assert calls == []
    i18n.set_language(i18n.LANG_EN)  # change → notify once
    assert calls == [1]
    i18n.set_language(i18n.LANG_ZH)
