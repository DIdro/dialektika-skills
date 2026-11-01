from click.testing import CliRunner
from pipeline_cli.cli import main


def test_help():
    r = CliRunner().invoke(main, ["--help"])
    assert r.exit_code == 0
    assert "upload" in r.output


def test_admin_help():
    r = CliRunner().invoke(main, ["admin", "--help"])
    assert r.exit_code == 0
    assert "add-user" in r.output
