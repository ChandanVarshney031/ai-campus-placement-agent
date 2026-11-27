import pytest
from app import app
from utils.i18n import (
    SUPPORTED_LOCALES,
    DEFAULT_LOCALE,
    LOCALE_NAMES,
    get_translation,
    get_all_translations_for_locale,
    TRANSLATION_CATALOG,
)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        with app.app_context():
            yield client


def test_i18n_translation_catalog_structure():
    """Verify translation catalog has all supported languages and default keys."""
    assert DEFAULT_LOCALE in SUPPORTED_LOCALES
    for locale in SUPPORTED_LOCALES:
        assert locale in TRANSLATION_CATALOG
        assert locale in LOCALE_NAMES
        translations = get_all_translations_for_locale(locale)
        assert isinstance(translations, dict)
        assert "app_title" in translations
        assert "nav_login" in translations
        assert "nav_register" in translations


def test_get_translation_fallback():
    """Test translation fallback logic for missing keys and invalid locales."""
    # Existing key in Hindi
    hi_title = get_translation("app_title", locale="hi")
    assert "एआई कैंपस प्लेसमेंट एजेंट" in hi_title

    # Missing key with fallback to English
    eng_val = get_translation("app_title", locale="invalid_code")
    assert eng_val == TRANSLATION_CATALOG["en"]["app_title"]

    # Completely non-existent key with default fallback string
    custom_default = get_translation("non_existent_key_xyz", locale="hi", default="Fallback String")
    assert custom_default == "Fallback String"


def test_set_language_endpoint_valid_locale(client):
    """Test switching languages via the /set-language/<lang_code> endpoint."""
    response = client.get("/set-language/hi", follow_redirects=True)
    assert response.status_code == 200

    # Ensure session was updated with the requested locale
    with client.session_transaction() as sess:
        assert sess.get("locale") == "hi"

    # Also check Spanish switch
    response = client.get("/set-language/es", follow_redirects=True)
    assert response.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("locale") == "es"


def test_set_language_endpoint_invalid_locale_fallback(client):
    """Test /set-language with unsupported locale retains previous or default."""
    client.get("/set-language/en", follow_redirects=True)
    response = client.get("/set-language/unsupported_lang", follow_redirects=True)
    assert response.status_code == 200
    with client.session_transaction() as sess:
        # Unsupported locale should not overwrite valid locale
        assert sess.get("locale") == "en"


def test_api_translations_all_and_locale_specific(client):
    """Test /api/translations endpoints return valid JSON catalog."""
    # Test current session translation API
    response = client.get("/api/translations")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "success"
    assert "translations" in data
    assert data["locale"] == DEFAULT_LOCALE

    # Test specific locale endpoint
    response = client.get("/api/translations/te")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "success"
    assert data["locale"] == "te"
    assert "ఏఐ క్యాంపస్ ప్లేస్‌మెంట్ ఏజెంట్" in data["translations"]["app_title"]


def test_rendered_page_reflects_locale(client):
    """Test index page rendering includes localized strings based on session locale."""
    # Set language to Hindi
    client.get("/set-language/hi", follow_redirects=True)
    res = client.get("/")
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert "हर कदम पर बेहतर तैयारी करें।" in html
    assert "लॉग आउट" in html or "लॉग इन करें" in html

    # Switch back to English
    client.get("/set-language/en", follow_redirects=True)
    res = client.get("/")
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert "Get placement-ready, one focused step at a time." in html
