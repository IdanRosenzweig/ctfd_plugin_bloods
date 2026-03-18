from flask import Blueprint, render_template, url_for, send_from_directory
from flask import current_app as app
from CTFd.models import db, Challenges, Solves, Awards, Users
from CTFd.utils.user import get_current_user
from CTFd.utils.decorators import authed_only, admins_only
from CTFd.utils.modes import USERS_MODE, TEAMS_MODE
from CTFd.utils import config, get_config
import datetime
import os

first_blood = Blueprint(
    "first_blood",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/plugins/first_blood/static",  # ← this makes /plugins/first_blood/static/... work
)

first_blood_value = 20


def load(app):
    # =============================================
    #  HOOK: Award first blood when a solve is created
    # =============================================
    def award_first_blood(challenge, solve):
        # Check if this is really the first solve
        first_solve = (
            Solves.query.filter_by(challenge_id=challenge.id)
            .order_by(Solves.date.asc())
            .first()
        )

        if first_solve and first_solve.id == solve.id:
            user = Users.query.filter_by(id=solve.account_id).first()
            if not user:
                return

            award = Awards(
                name=f"First Blood — {challenge.name}",
                description=f"First to solve {challenge.name}",
                value=first_blood_value,
                category="First Blood",
                icon="first-blood.svg",  # relative to static/
                user_id=user.id,
                team_id=user.team_id if config.user_mode() == TEAMS_MODE else None,
                date=solve.date,
            )
            db.session.add(award)
            db.session.commit()

    app.events.subscribe("on_challenge_solve", award_first_blood)

    # =============================================
    #  PUBLIC PAGE: /firstbloods
    # =============================================
    @first_blood.route("/firstbloods")
    def firstbloods():
        awards = (
            Awards.query.filter(Awards.category == "First Blood")
            .order_by(Awards.date.desc())
            .all()
        )

        results = []
        for award in awards:
            challenge = Challenges.query.get(
                award.challenge_id
            )  # may be None if deleted
            user = Users.query.get(award.user_id)

            entry = {
                "award": award,
                "challenge": challenge,
                "user": user,
                "date": award.date,
                "challenge_name": (
                    challenge.name if challenge else "[deleted challenge]"
                ),
                "challenge_id": challenge.id if challenge else None,
                "icon": award.icon or "first-blood.svg",
            }
            results.append(entry)

        return render_template(
            "first_bloods.html",
            first_bloods=results,
            user_mode=config.user_mode(),
            ctf_name=app.config["CTF_NAME"],
        )

    # =============================================
    #  ADMIN: Regenerate first bloods (optional)
    # =============================================
    @first_blood.route("/admin/firstblood/regenerate", methods=["POST"])
    @admins_only
    def regenerate_first_bloods():
        Awards.query.filter_by(category="First Blood").delete()
        db.session.commit()

        all_solves = Solves.query.order_by(Solves.date.asc()).all()
        seen = set()

        for solve in all_solves:
            chal_id = solve.challenge_id
            if chal_id in seen:
                continue
            challenge = Challenges.query.get(chal_id)
            if not challenge:
                continue
            user = Users.query.get(solve.account_id)
            if not user:
                continue

            award = Awards(
                name=f"First Blood — {challenge.name}",
                description=f"First to solve {challenge.name}",
                value=first_blood_value,
                category="First Blood",
                icon="first-blood.svg",
                user_id=user.id,
                team_id=user.team_id if config.user_mode() == TEAMS_MODE else None,
                date=solve.date,
            )
            db.session.add(award)
            seen.add(chal_id)

        db.session.commit()
        return {
            "success": True,
            "message": f"Regenerated {len(seen)} first blood awards.",
        }

    app.register_blueprint(first_blood)

    # Optional: Add menu item
    def register_menu():
        return {
            "text": "First Bloods",
            "link": url_for("first_blood.firstbloods"),
            "type": "public",
            "icon": "fa-trophy",
        }

    # If your CTFd version supports plugin menu registration:
    # app.pb.register_menu_item("mainbar", "firstbloods", register_menu)


def bless():
    pass  # Optional migration hook if needed later
