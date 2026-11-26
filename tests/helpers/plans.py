"""Small plan builders shared by the apply tests."""

from tidyshelf.plan import Plan


def movie_plan(env, item_id: str = "movie-0001", name: str = "Some.Film.1999.1080p") -> Plan:
    """A typical ready item: one video into a new movie folder plus a junk delete."""
    env.tree("source", f"{name}/film.mkv [100]\n{name}/readme.txt [5]\n")
    plan = Plan()
    item = plan.item(item_id, "movie", source=f"source:{name}")
    item.mkdir("movies:Some Film (1999)")
    item.move(f"source:{name}/film.mkv", "movies:Some Film (1999)/Some Film (1999).mkv",
              *env.stat(f"source:{name}/film.mkv"))
    item.delete_file(f"source:{name}/readme.txt", *env.stat(f"source:{name}/readme.txt"), reason="junk")
    item.delete_dir(f"source:{name}", reason="empty")
    return plan
