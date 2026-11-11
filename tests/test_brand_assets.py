from pathlib import Path


def test_navigation_brand_uses_profile_photo_and_white_asdev_signature():
    html = Path("app/static/index.html").read_text()
    css = Path("app/static/style.css").read_text()

    assert 'aria-label="ASdev, inicio"' in html
    assert 'src="/static/profile-avatar.jpg"' in html
    assert 'src="/static/asdev-signature-thick.png"' in html
    assert "filter:brightness(0) invert(1)" in css
    assert Path("app/static/profile-avatar.jpg").is_file()
    assert Path("app/static/asdev-signature-thick.png").is_file()


def test_navigation_places_configuration_before_secrets_and_removes_status_view():
    html = Path("app/static/index.html").read_text()
    script = Path("app/static/app.js").read_text()

    nav_start = html.index('<nav aria-label="Herramientas del asistente">')
    nav_end = html.index("</nav>", nav_start)
    config_position = html.index('id="config-open"')
    secrets_position = html.index('href="/secrets"')
    assert nav_start < config_position < secrets_position < nav_end
    assert 'href="/status"' not in html
    assert 'aria-label="Estado"' not in html
    assert 'id="status"' not in html
    assert "showStatus" not in script
    assert "'/status'" not in script


def test_projects_navigation_remains_available_when_branding_changes():
    html = Path("app/static/index.html").read_text()
    assert 'href="/projects"' in html
