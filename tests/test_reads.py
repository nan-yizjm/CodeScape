from codescape.reads import VersionReads


def available(reads, project="p", version="v"):
    with reads.reclaim(project, version) as value:
        return value


def test_clients_have_independent_expiring_leases_and_release_on_switch():
    clock = [0]
    reads = VersionReads(clock=lambda: clock[0])
    reads.renew("one", [("p", "v", 60)])
    reads.renew("two", [("p", "v", 60)])
    reads.disconnect("one")
    assert not available(reads)
    reads.renew("two", [("p", "new", 60)])
    assert available(reads)
    assert not available(reads, version="new")
    clock[0] = 61
    assert available(reads, version="new")
    assert not reads.held("two", "p", "new")


def test_active_read_protects_snapshot_even_after_lease_expiry():
    clock = [0]
    reads = VersionReads(clock=lambda: clock[0])
    reads.renew("one", [("p", "v", 1)])
    with reads.read("p", "v"):
        clock[0] = 2
        assert not reads.held("one", "p", "v")
        assert not available(reads)
    assert available(reads)
    assert not reads.readers


def test_retired_lease_cannot_extend_past_hard_limit_or_revocation():
    clock = [0]
    reads = VersionReads(clock=lambda: clock[0])
    reads.renew("one", [("p", "v", 5)])
    clock[0] = 6
    assert available(reads)
    reads.revoke("p")
    reads.renew("one", [("p", "v", 60)])
    assert not reads.held("one", "p", "v")
    import pytest
    with pytest.raises(ValueError, match="删除"):
        with reads.read("p", "v"):
            pass
