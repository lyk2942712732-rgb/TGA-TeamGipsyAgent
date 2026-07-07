from tga.core.scope import is_in_scope


def test_host_port_scope_matches():
    assert is_in_scope("http://127.0.0.1:8080/login", ["127.0.0.1:8080"])


def test_out_of_scope_rejected():
    assert not is_in_scope("http://127.0.0.1:9000", ["127.0.0.1:8080"])

