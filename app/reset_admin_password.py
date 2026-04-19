#!/usr/bin/env python3
"""
管理者パスワード緊急リセットスクリプト

管理者（Admin）がパスワードを忘れた場合に、サーバー上で直接実行して
パスワードをリセットします。

使い方:
  Docker環境:  docker exec -it skillmap python reset_admin_password.py
  直接実行:    cd app && python reset_admin_password.py
"""

import sys
import getpass

# アプリと同じディレクトリで実行されることを想定
import models
import auth
from database import SessionLocal, engine

models.Base.metadata.create_all(bind=engine)


def main():
    db = SessionLocal()
    try:
        admins = db.query(models.User).filter(models.User.role == "admin").all()

        if not admins:
            print("\n❌ 管理者アカウントが見つかりません。")
            print("   /setup からセットアップを行ってください。")
            sys.exit(1)

        print("\n" + "=" * 50)
        print("  🔑 管理者パスワード緊急リセット")
        print("=" * 50)

        if len(admins) == 1:
            target = admins[0]
            print(f"\n  管理者: {target.username} ({target.display_name or '-'})")
        else:
            print("\n  管理者アカウント一覧:")
            for i, a in enumerate(admins, 1):
                print(f"    {i}. {a.username} ({a.display_name or '-'})")

            while True:
                choice = input(f"\n  リセットする管理者の番号を選択 [1-{len(admins)}]: ").strip()
                if choice.isdigit() and 1 <= int(choice) <= len(admins):
                    target = admins[int(choice) - 1]
                    break
                print("  ⚠  有効な番号を入力してください。")

        print(f"\n  対象: {target.username}")
        print("-" * 50)

        while True:
            new_pw = getpass.getpass("  新しいパスワード (6文字以上): ")
            if len(new_pw) < 6:
                print("  ⚠  パスワードは6文字以上にしてください。")
                continue
            confirm = getpass.getpass("  パスワード確認: ")
            if new_pw != confirm:
                print("  ⚠  パスワードが一致しません。もう一度入力してください。")
                continue
            break

        target.password_hash = auth.hash_password(new_pw)
        db.commit()

        print("\n  ✅ パスワードをリセットしました！")
        print(f"  ユーザー名: {target.username}")
        print(f"  ログイン画面からログインしてください。")
        print("=" * 50 + "\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
