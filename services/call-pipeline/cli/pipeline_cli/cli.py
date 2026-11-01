import click
from .commands import (upload, list_cmd, show_cmd, skip_cmd, retry_cmd, status_cmd,
                       watch, record, recordings_cmd, record_stop, record_transcript)
from .commands.admin import add_user, rotate_token, disk_status, tail_logs


@click.group()
@click.version_option()
def main():
    """call-pipeline CLI"""


main.add_command(upload.upload)
main.add_command(list_cmd.list_)
main.add_command(show_cmd.show)
main.add_command(skip_cmd.skip)
main.add_command(retry_cmd.retry)
main.add_command(status_cmd.status)
main.add_command(watch.watch)
main.add_command(record.record)
main.add_command(recordings_cmd.recordings)
main.add_command(record_stop.record_stop)
main.add_command(record_transcript.record_transcript)


@main.group()
def admin():
    """Admin commands"""


admin.add_command(add_user.add_user)
admin.add_command(rotate_token.rotate_token)
admin.add_command(disk_status.disk_status)
admin.add_command(tail_logs.tail_logs)
