from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from support.launch_help_manifest import LAUNCH_CATEGORIES, LAUNCH_HELP_ARTICLES
from support.models import HelpArticle, TicketCategory
from support.taxonomy import REFUND_CATEGORY_SLUG


LEGACY_REFUND_CATEGORY_SLUG = "refund"


class Command(BaseCommand):
    help = "Plan or safely apply the canonical launch Help Center content. Defaults to a dry run."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Persist the planned changes.")

    def handle(self, *args, **options):
        self._validate_environment()
        apply = options["apply"]
        plan = self._build_plan()
        self._write_plan(plan, apply)
        if not apply:
            self.stdout.write(self.style.WARNING("Dry run complete; no persistent changes were made."))
            return

        with transaction.atomic():
            categories = self._apply_categories(plan["categories"])
            self._apply_articles(plan["articles"], categories)
        self.stdout.write(self.style.SUCCESS("Launch Help Center seed applied successfully."))

    def _validate_environment(self):
        environment = getattr(settings, "APP_ENV", "development")
        if environment == "production":
            raise CommandError("seed_launch_help is forbidden in production.")
        if environment == "local_production" and connection.vendor != "postgresql":
            raise CommandError("seed_launch_help requires PostgreSQL in local_production.")

    def _build_plan(self):
        categories = []
        manifest_slugs = {item.slug for item in LAUNCH_CATEGORIES}
        legacy = TicketCategory.objects.filter(slug=LEGACY_REFUND_CATEGORY_SLUG).first()
        canonical = TicketCategory.objects.filter(slug=REFUND_CATEGORY_SLUG).first()
        if legacy and canonical and legacy.pk != canonical.pk:
            raise CommandError(
                "Both legacy 'refund' and canonical 'returns-refunds' categories exist; "
                "refusing to guess which record owns existing tickets."
            )

        for item in LAUNCH_CATEGORIES:
            existing = canonical if item.slug == REFUND_CATEGORY_SLUG else TicketCategory.objects.filter(slug=item.slug).first()
            if item.slug == REFUND_CATEGORY_SLUG and existing is None:
                existing = legacy
            if existing is None:
                action = "create"
            elif item.slug == REFUND_CATEGORY_SLUG and existing.slug == LEGACY_REFUND_CATEGORY_SLUG:
                action = "normalize"
            elif self._category_matches(existing, item):
                action = "skip"
            else:
                action = "skip_operator_content"
            categories.append((action, item, existing))

        articles = []
        for item in LAUNCH_HELP_ARTICLES:
            existing = HelpArticle.objects.filter(slug=item.slug).first()
            if existing is None:
                action = "create"
            elif self._article_matches(existing, item):
                # An exact content match is the safe ownership signal available
                # without adding schema. Repair the publication timestamp only
                # for such a manifest-owned record.
                action = "update" if item.is_published and existing.published_at is None else "skip"
            else:
                action = "skip_operator_content"
            articles.append((action, item, existing))
        return {"categories": categories, "articles": articles, "manifest_slugs": manifest_slugs}

    @staticmethod
    def _category_matches(category, item):
        return all(getattr(category, field) == getattr(item, field) for field in ("slug", "name", "name_fa", "description", "description_fa", "icon"))

    @staticmethod
    def _article_matches(article, item):
        return (
            article.category.slug == item.category_slug
            and all(getattr(article, field) == getattr(item, field) for field in (
                "slug", "title", "title_fa", "summary", "summary_fa", "content", "content_fa", "is_featured", "is_published"
            ))
        )

    def _write_plan(self, plan, apply):
        category_counts = Counter(action for action, *_ in plan["categories"])
        article_counts = Counter(action for action, *_ in plan["articles"])
        mode = "Apply" if apply else "Plan"
        self.stdout.write(f"{mode}: categories {dict(category_counts)}; help articles {dict(article_counts)}.")
        for action, item, _existing in plan["categories"]:
            if action != "skip":
                self.stdout.write(f"  category {action}: {item.slug}")
        for action, item, _existing in plan["articles"]:
            if action != "skip":
                self.stdout.write(f"  guide {action}: {item.slug}")

    def _apply_categories(self, category_plan):
        categories = {}
        for action, item, existing in category_plan:
            if action == "create":
                existing = TicketCategory.objects.create(
                    slug=item.slug, name=item.name, name_fa=item.name_fa,
                    description=item.description, description_fa=item.description_fa, icon=item.icon,
                )
            elif action == "normalize":
                # Update this exact record in place: Ticket.category uses PROTECT,
                # so every existing refund conversation retains its foreign key.
                existing.slug = item.slug
                existing.name = item.name
                existing.name_fa = item.name_fa
                existing.description = item.description
                existing.description_fa = item.description_fa
                existing.icon = item.icon
                existing.save(update_fields=("slug", "name", "name_fa", "description", "description_fa", "icon"))
            categories[item.slug] = existing
        return categories

    def _apply_articles(self, article_plan, categories):
        published_at = timezone.now()
        for action, item, _existing in article_plan:
            if action == "update":
                _existing.published_at = published_at
                _existing.save(update_fields=("published_at",))
                continue
            if action != "create":
                continue
            HelpArticle.objects.create(
                category=categories[item.category_slug], slug=item.slug,
                title=item.title, title_fa=item.title_fa,
                summary=item.summary, summary_fa=item.summary_fa,
                content=item.content, content_fa=item.content_fa,
                is_published=item.is_published, is_featured=item.is_featured,
                published_at=published_at if item.is_published else None,
            )
