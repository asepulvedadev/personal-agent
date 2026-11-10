"""The settings shell preserves the native fixed-file editor contract."""
from html.parser import HTMLParser
from pathlib import Path

from test_portal import portal, login


class Elements(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=[]; self.nodes=[]
    def handle_starttag(self, tag, attrs):
        values=dict(attrs); self.nodes.append((tag,values))
        if 'id' in values:self.ids.append(values['id'])


def test_settings_shell_assets_and_no_duplicate_ids(portal):
    _,c=portal; login(c)
    page=c.get('/settings'); assert page.status_code==200
    parser=Elements(); parser.feed(page.text)
    assert len(parser.ids)==len(set(parser.ids))
    for name in ['integration-credential','asset-profile','asset-kind','assistant-form','config-dialog']:
        assert name in parser.ids
    assert 'data-settings-nav="agents"' in page.text
    assert 'data-settings-nav="integrations"' in page.text
    assert 'data-settings-nav="customization"' in page.text
    for url in ['/static/settings-hub.css','/static/settings-hub.js']:
        assert c.get(url).status_code==200


def test_image_contains_native_editor_and_keeps_single_transport():
    root=Path(__file__).parents[1]
    docker=(root/'deploy/Dockerfile.hermes').read_text()
    assert 'runtime/config_files.py runtime/customization.py /opt/runtime/' in docker
    assert (root/'runtime/patch_config_files.py').read_text().count('_custom_module.register_customization(self._app, self)')==1
    script=(root/'app/static/settings-hub.js').read_text()
    assert "$('integration-credential').value=''" in script
    assert 'textContent=data.reply' not in script  # Uses the text-only conversation renderer.
    assert "conversation('Configurador',data.reply)" in script
