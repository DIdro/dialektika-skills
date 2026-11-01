import click


@click.command(name="retry")
@click.argument("job_id")
def retry(job_id: str):
    """Retry a failed job (server-side endpoint pending — re-upload as workaround)."""
    click.echo(
        f"Retry not yet wired to server. To retry job {job_id}, "
        "delete it (`pipeline skip {job_id}`) and `pipeline upload` the same file again."
    )
