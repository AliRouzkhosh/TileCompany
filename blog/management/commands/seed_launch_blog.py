from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from blog.launch_blog_manifest import ARTICLES, CATEGORIES, TAGS
from blog.models import Article, ArticleStatus, BlogCategory, BlogTag
from products.models import Product


class Command(BaseCommand):
    help = "Plan or safely apply the bilingual launch editorial pack (dry run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        env = getattr(settings, "APP_ENV", "development")
        if env == "production": raise CommandError("seed_launch_blog is forbidden in production.")
        if env == "local_production" and connection.vendor != "postgresql":
            raise CommandError("seed_launch_blog requires PostgreSQL in local_production.")
        plan = self._plan()
        self.stdout.write(f"{'Apply' if options['apply'] else 'Plan'}: " + "; ".join(f"{key} {dict(Counter(row[0] for row in value))}" for key, value in plan.items()))
        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run complete; no persistent changes were made.")); return
        with transaction.atomic():
            categories = self._categories(plan["categories"])
            tags = self._tags(plan["tags"])
            self._articles(plan["articles"], categories, tags)
        self.stdout.write(self.style.SUCCESS("Launch editorial pack applied successfully."))

    def _plan(self):
        def action(existing, fields): return "create" if existing is None else ("skip" if all(getattr(existing, key) == value for key, value in fields.items()) else "skip_operator_content")
        categories = [(action(BlogCategory.objects.filter(slug=item.slug).first(), item.__dict__), item, BlogCategory.objects.filter(slug=item.slug).first()) for item in CATEGORIES]
        tags = [(action(BlogTag.objects.filter(slug=item.slug).first(), item.__dict__), item, BlogTag.objects.filter(slug=item.slug).first()) for item in TAGS]
        articles = []
        for item in ARTICLES:
            existing = Article.objects.filter(slug=item.slug).first()
            fields = {key: getattr(item, key) for key in ("title", "title_fa", "summary", "summary_fa", "content", "content_fa", "meta_title", "meta_title_fa", "meta_description", "meta_description_fa")}
            state = "create" if existing is None else ("repair_published_at" if all(getattr(existing, key) == value for key, value in fields.items()) and existing.status == ArticleStatus.PUBLISHED and existing.published_at is None else ("skip" if all(getattr(existing, key) == value for key, value in fields.items()) else "skip_operator_content"))
            articles.append((state, item, existing))
        return {"categories": categories, "tags": tags, "articles": articles}

    def _categories(self, rows):
        result = {}
        for state, item, existing in rows:
            result[item.slug] = existing or BlogCategory.objects.create(**item.__dict__)
        return result

    def _tags(self, rows):
        result = {}
        for state, item, existing in rows:
            result[item.slug] = existing or BlogTag.objects.create(**item.__dict__)
        return result

    def _articles(self, rows, categories, tags):
        for state, item, existing in rows:
            if state == "repair_published_at": existing.save(); continue
            if state != "create": continue
            article = Article.objects.create(
                slug=item.slug, category=categories[item.category_slug], author=None, status=ArticleStatus.PUBLISHED,
                title=item.title, title_fa=item.title_fa, summary=item.summary, summary_fa=item.summary_fa,
                content=item.content, content_fa=item.content_fa, meta_title=item.meta_title, meta_title_fa=item.meta_title_fa,
                meta_description=item.meta_description, meta_description_fa=item.meta_description_fa,
            )
            article.tags.set(tags[slug] for slug in item.tag_slugs)
            products = Product.objects.filter(slug__in=item.product_slugs, is_active=True)
            article.related_products.set(products)
