from django.core.management.base import BaseCommand
from blog.models import Article
from blog.sanitization import sanitize_article_html

class Command(BaseCommand):
    help = "Report Article bodies that differ from the safe editorial HTML contract."
    def add_arguments(self, parser): parser.add_argument("--fix", action="store_true")
    def handle(self, *args, **options):
        changed = 0
        for article in Article.objects.all().only("id", "slug", "content", "content_fa"):
            updates = {field: sanitize_article_html(getattr(article, field)) for field in ("content", "content_fa")}
            dirty = [field for field, value in updates.items() if value != getattr(article, field)]
            for field in dirty: self.stdout.write(f"{article.pk} {article.slug} {field}: sanitizer would modify")
            if dirty and options["fix"]:
                Article.objects.filter(pk=article.pk).update(**{field: updates[field] for field in dirty})
            changed += bool(dirty)
        self.stdout.write(f"{changed} article(s) require cleanup.")
