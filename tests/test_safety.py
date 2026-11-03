import pytest
from electronicload_tool_core.safety import load_safety_config
from electronicload_tool_core.errors import LoadError

def test_accepted_config(tmp_path):
    p=tmp_path/'limits.toml';p.write_text('[safety]\nmax_current = 2.0\n')
    assert load_safety_config(str(p)).max_current==2

@pytest.mark.parametrize('value',['-1','0','61','false','"2"','nan','inf'])
def test_invalid_limit(tmp_path,value):
    p=tmp_path/'limits.toml';p.write_text('[safety]\nmax_current = '+value+'\n')
    with pytest.raises(LoadError):load_safety_config(str(p))

def test_missing_refuses():
    with pytest.raises(LoadError):load_safety_config(None)
