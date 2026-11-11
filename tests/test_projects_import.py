"""Word tables are evidence, never executable instructions or completed work."""
import importlib.util
from pathlib import Path
from zipfile import ZipFile
import pytest


def script():
    spec=importlib.util.spec_from_file_location('import_project_source',Path(__file__).parents[1]/'scripts/import_project_source.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_import_keeps_proposal_why_benefit_and_pending_state(tmp_path):
    path=tmp_path/'source.docx'
    cells=['A1','Check the inbox','Answered work appears pending','Clear next actions','Alta']
    xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+value+'</w:t></w:r></w:p></w:tc>' for value in cells)+'</w:tr></w:tbl></w:body></w:document>'
    with ZipFile(path,'w') as archive: archive.writestr('word/document.xml',xml)
    result=script().extract_proposals(path)
    assert len(result)==1
    task=result[0]
    assert task['code']=='A1' and task['why']==cells[2] and task['benefit']==cells[3]
    assert task['proposal']==cells[1] and task['approval']=='pending' and task['status']=='todo'
    assert 'resolution' not in task and 'time_entries' not in task


def test_import_rejects_entities(tmp_path):
    path=tmp_path/'source.docx'
    with ZipFile(path,'w') as archive: archive.writestr('word/document.xml','<!DOCTYPE x [<!ENTITY a "x">]><x/>')
    with pytest.raises(ValueError): script().extract_proposals(path)
