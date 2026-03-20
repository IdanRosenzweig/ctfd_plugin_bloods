import datetime
from flask import Blueprint, render_template, request
from CTFd.models import db, Solves, Awards, Challenges, Users, Teams, Configs
from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only
from CTFd.plugins import register_user_page_menu_bar, register_admin_plugin_menu_bar
from CTFd.cache import clear_standings  # <-- Added cache clearer


class BloodAward(db.Model):
    __tablename__ = "blood_awards"
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE")
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"))
    award_id = db.Column(db.Integer, db.ForeignKey("awards.id", ondelete="CASCADE"))
    position = db.Column(db.Integer)


def init_default_configs():
    """sets the default plugin config if it doesn't exist yet"""
    
    if get_config("bloods_no_bloods") is None:
        # default number of blood positions
        set_config("bloods_no_bloods", "3")

        # default bloods parameters
        defaults = {
            "bloods_bonus_1": "20",
            "bloods_title_1": "First Blood",
            "bloods_icon_1": "lightning",
            
            "bloods_bonus_2": "10",
            "bloods_title_2": "Second Blood",
            "bloods_icon_2": "lightning",
            
            "bloods_bonus_3": "5",
            "bloods_title_3": "Third Blood",
            "bloods_icon_3": "lightning",
            
            "bloods_filter_mode": "blacklist",
            "bloods_filter_list": "",
        }
        for k, v in defaults.items():
            set_config(k, v)

def reset_default_configs():
    """resets the plugin config"""
    
    # default number of blood positions
    set_config("bloods_no_bloods", "3")

    # default bloods parameters
    defaults = {
        "bloods_bonus_1": "20",
        "bloods_title_1": "First Blood",
        "bloods_icon_1": "lightning",
        
        "bloods_bonus_2": "10",
        "bloods_title_2": "Second Blood",
        "bloods_icon_2": "lightning",
        
        "bloods_bonus_3": "5",
        "bloods_title_3": "Third Blood",
        "bloods_icon_3": "lightning",
        
        "bloods_filter_mode": "blacklist",
        "bloods_filter_list": "",
    }
    for k, v in defaults.items():
        set_config(k, v)

    # erase all previous bloods configs
    all_bloods_configs = Configs.query.filter(Configs.key.like("bloods_%")).all()
    
    for config_row in all_bloods_configs:
        parts = config_row.key.split("_")
        
        if parts[-1].isdigit():
            position = int(parts[-1])
            if position > 3:
                db.session.delete(config_row)
                
    db.session.commit() # commit the delete changes to the database
    

def sync_all_bloods():
    """Strictly enforces that the awards perfectly match the current top solvers and configuration."""
    user_mode = get_config("user_mode")
    challenges = Challenges.query.all()

    # retrieve no_bloods
    no_bloods = int(get_config("bloods_no_bloods") or 3)

    # retrieve the current config
    bonuses = {}
    titles = {}
    icons = {}

    for i in range(1, no_bloods + 1):
        bonuses[i] = int(get_config(f"bloods_bonus_{i}") or 0)

        default_title = (
            "First Blood"
            if i == 1
            else (
                "Second Blood"
                if i == 2
                else "Third Blood" if i == 3 else f"{i}th Blood"
            )
        )
        titles[i] = get_config(f"bloods_title_{i}") or default_title
        icons[i] = get_config(f"bloods_icon_{i}") or "lightning"

    filter_mode = get_config("bloods_filter_mode")
    filter_raw = get_config("bloods_filter_list") or ""
    filter_list = [name.strip() for name in filter_raw.split(",") if name.strip()]

    # sync bloods for each chal
    for chal in challenges:
      
      # check if this chal has blood
        chal_has_blood: bool = False
        if filter_mode == "blacklist":
            chal_has_blood = chal.name not in filter_list
        elif filter_mode == "whitelist":
            chal_has_blood = chal.name in filter_list
        else:
            raise ValueError(f"Invalid filter mode: {filter_mode}")

        # chal doesn't have blood, destroy any existing awards (if it has any)
        if not chal_has_blood:
            trackers = BloodAward.query.filter_by(challenge_id=chal.id).all()
            for tracker in trackers:
                award = Awards.query.filter_by(id=tracker.award_id).first()
                if award:
                    db.session.delete(award)
                db.session.delete(tracker)
            db.session.commit()
            continue

        # find the solutions for this chal
        query = Solves.query.join(Users, Solves.user_id == Users.id).filter(
            Solves.challenge_id == chal.id, Users.banned == False, Users.hidden == False
        )

        if user_mode == "teams":
            query = query.join(Teams, Solves.team_id == Teams.id).filter(
                Teams.banned == False, Teams.hidden == False
            )

        # find the top solutions (the first solutions)
        top_solves = (
            query.order_by(Solves.date.asc(), Solves.id.asc())
            .limit(no_bloods)
            .all()
        )
        
        valid_state = {i + 1: solve for i, solve in enumerate(top_solves)}

        trackers = BloodAward.query.filter_by(challenge_id=chal.id).all()
        valid_positions_kept = []

        # destroy or update existing awards
        for tracker in trackers:
            if tracker.position > no_bloods:
                award = Awards.query.filter_by(id=tracker.award_id).first()
                if award:
                    db.session.delete(award)
                db.session.delete(tracker)
                continue

            expected_solve = valid_state.get(tracker.position)

            if (
                expected_solve
                and tracker.user_id == expected_solve.user_id
                and tracker.team_id == expected_solve.team_id
            ):
                award = Awards.query.filter_by(id=tracker.award_id).first()
                if award:
                    award.name = titles[tracker.position]
                    award.description = f"{chal.name}"
                    award.value = bonuses[tracker.position]
                    award.icon = icons[tracker.position]
                    award.date = expected_solve.date
                    valid_positions_kept.append(tracker.position)
                else:
                    db.session.delete(tracker)
            else:
                award = Awards.query.filter_by(id=tracker.award_id).first()
                if award:
                    db.session.delete(award)
                db.session.delete(tracker)

        db.session.commit()

        # add missing awards
        for pos, solve in valid_state.items():
            if pos not in valid_positions_kept:
                award = Awards(
                    user_id=solve.user_id,
                    team_id=solve.team_id,
                    name=titles[pos],
                    description=f"{chal.name}",
                    value=bonuses[pos],
                    icon=icons[pos],
                    date=solve.date,
                )
                db.session.add(award)
                db.session.commit()

                new_tracker = BloodAward(
                    challenge_id=chal.id,
                    user_id=solve.user_id,
                    team_id=solve.team_id,
                    award_id=award.id,
                    position=pos,
                )
                db.session.add(new_tracker)
                db.session.commit()

def load(app):
    app.db.create_all()

    # on startup, init default config and perform initial sync
    with app.app_context():
        try:
            init_default_configs()
            sync_all_bloods()
            print("[Bloods Plugin] Initial sync completed successfully on startup!")
        except Exception as e:
            print(f"[Bloods Plugin] Initial sync failed during startup: {e}")

    bloods_bp = Blueprint("bloods", __name__, template_folder="templates")

    # bloods admin page
    @bloods_bp.route("/admin/bloods", methods=["GET", "POST"])
    @admins_only
    def admin_bloods_config():
        if request.method == "POST":
            # Check which button was clicked
            action = request.form.get("action")

            if action == "reset":
                reset_default_configs()
                sync_all_bloods()
                return render_template(
                    "admin_bloods.html",
                    success=True,
                    message="Settings have been reset to defaults and database re-synced!",
                    no_bloods=int(get_config("bloods_no_bloods") or 3),
                )
            else:
                # Normal save action
                for key in request.form:
                    if key.startswith("bloods_"):
                        set_config(key, request.form[key])

                sync_all_bloods()
                return render_template(
                    "admin_bloods.html",
                    success=True,
                    message="Settings updated and all awards have been re-synced!",
                    no_bloods=int(get_config("bloods_no_bloods") or 3),
                )

        # Pass the current no_bloods to the template so it knows how many rows to render
        return render_template(
            "admin_bloods.html",
            no_bloods=int(get_config("bloods_no_bloods") or 3),
        )

    # bloods page
    @bloods_bp.route("/bloods", methods=["GET"])
    def bloods_page():
        bloods_data = BloodAward.query.all()
        bloods = []

        for b in bloods_data:
            chal = Challenges.query.get(b.challenge_id)
            if not chal or chal.state != "visible":
                continue

            user = Users.query.get(b.user_id)
            team = Teams.query.get(b.team_id) if b.team_id else None
            solve = Solves.query.filter_by(
                challenge_id=b.challenge_id, user_id=b.user_id
            ).first()

            bloods.append(
                {
                    "challenge_name": chal.name,
                    "challenge_id": chal.id,
                    "user_name": user.name if user else "Unknown",
                    "user_id": user.id if user else None,
                    "team_name": team.name if team else "None",
                    "team_id": team.id if team else None,
                    "date": solve.date if solve else None,
                    "position": b.position,
                }
            )

        bloods.sort(
            key=lambda x: x["date"] if x["date"] else datetime.datetime.min,
            reverse=True,
        )
        return render_template("bloods.html", bloods=bloods)

    app.register_blueprint(bloods_bp)

    register_admin_plugin_menu_bar("Bloods Config", "/admin/bloods")
    register_user_page_menu_bar("Bloods", "/bloods")

    # perform sync on every action that could have caused a relevant change
    @app.after_request
    def trigger_bloods_sync(response):
        if request.method in ["POST", "PATCH", "DELETE"]:
            path = request.path
            if (
                path.startswith("/api/v1/challenges/attempt")
                or path.startswith("/api/v1/users")
                or path.startswith("/api/v1/teams")
                or path.startswith("/api/v1/solves")
            ):
                try:
                    sync_all_bloods()
                except Exception as e:
                    print(f"[Bloods Plugin] Sync Error: {e}")
        return response
    
