from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from django.utils import timezone
from blog.models import Article, ArticleStatus, BlogCategory, BlogTag
import random


class Command(BaseCommand):
    help = "Generate 5 sample blog articles"

    def handle(self, *args, **kwargs):
        self.stderr.write(self.style.WARNING("seed_articles is deprecated and does not run. Use seed_launch_blog (dry run by default)."))
        return
        User = get_user_model()
        author = User.objects.filter(is_superuser=True).first() or User.objects.first()

        if not author:
            self.stdout.write(self.style.ERROR("No users found. Create a user first."))
            return

        categories = {
            name: BlogCategory.objects.get_or_create(
                name=name,
                defaults={
                    "slug": slugify(name),
                    "description": f"{name} guides and inspiration for TileStore customers.",
                },
            )[0]
            for name in ("Buying Guides", "Design Ideas", "Tile Care")
        }

        tags = {
            name: BlogTag.objects.get_or_create(
                name=name,
                defaults={"slug": slugify(name)},
            )[0]
            for name in ("porcelain", "ceramic", "bathroom", "kitchen", "flooring")
        }

        articles = [
            ("How to Choose the Right Tile for Small Bathrooms", "Buying Guides", ["bathroom", "ceramic"]),
            ("Top Modern Tile Trends for 2026", "Design Ideas", ["porcelain", "flooring"]),
            ("Matte vs Glossy Tiles: Which Finish Fits Your Space?", "Buying Guides", ["kitchen", "bathroom"]),
            ("Porcelain vs Ceramic: A Practical Comparison", "Buying Guides", ["porcelain", "ceramic"]),
            ("How to Clean and Maintain Kitchen Backsplash Tiles", "Tile Care", ["kitchen", "ceramic"]),
        ]

        created_count = 0
        for i, (title, category_name, tag_names) in enumerate(articles, start=1):
            base_slug = slugify(title)
            slug = base_slug
            n = 2
            while Article.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{n}"
                n += 1

            summary = (
                "A quick guide to help you make better tile decisions for style, durability, and budget."
            )

            content = f"""
{title}

Choosing tiles is about balancing design and performance.
In this guide, we cover key points to help homeowners and designers make smart choices.

1) Define the space requirements
Consider moisture, foot traffic, lighting, and maintenance.

2) Compare materials
Porcelain, ceramic, and natural stone each have strengths and trade-offs.

3) Pick the right finish
Matte surfaces hide smudges; glossy surfaces reflect light and feel brighter.

4) Think long-term
Use quality grout, proper installation, and regular cleaning for lasting results.

Final tip:
Always test a sample tile in your real lighting before final purchase.
""".strip()

            article = Article.objects.create(
                title=title,
                slug=slug,
                author=author,
                summary=summary,
                content=content,
                category=categories[category_name],
                status=ArticleStatus.PUBLISHED,
                published_at=timezone.now(),
            )
            article.tags.set(tags[name] for name in tag_names)
            created_count += 1
            self.stdout.write(self.style.SUCCESS(f"Created: {article.title} ({article.slug})"))

        self.stdout.write(self.style.SUCCESS(f"\nDone. {created_count} articles created."))
