import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "用 DJANGO_ADMIN_USER / DJANGO_ADMIN_PASSWORD 建立登入帳號（已存在就不動）"

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_ADMIN_USER")
        password = os.environ.get("DJANGO_ADMIN_PASSWORD")
        if not username or not password:
            self.stdout.write("DJANGO_ADMIN_USER / DJANGO_ADMIN_PASSWORD 沒設定，略過。")
            return
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"帳號 {username} 已存在。")
            return
        User.objects.create_superuser(username=username, email="", password=password)
        self.stdout.write(self.style.SUCCESS(f"已建立帳號 {username}。"))
