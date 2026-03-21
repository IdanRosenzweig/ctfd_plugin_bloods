import datetime
from flask import Blueprint, render_template, request
from CTFd.models import db, Solves, Awards, Challenges, Users, Teams, Configs
from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only
from CTFd.plugins import register_user_page_menu_bar, register_admin_plugin_menu_bar


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

# config keys prefix
config_key_prefix = "bloods_"

# config no_bloods
def config_key_no_bloods() -> str:
  return f"{config_key_prefix}no_bloods"

def config_get_no_bloods() -> str | None:
  return get_config(config_key_no_bloods()) or None

# config bloods
def config_key_blood_val(blood_num: int) -> str:
  return f"{config_key_prefix}blood_{blood_num}_val"

def config_key_blood_title(blood_num: int) -> str:
  return f"{config_key_prefix}blood_{blood_num}_title"

def config_key_blood_icon(blood_num: int) -> str:
  return f"{config_key_prefix}blood_{blood_num}_icon"

# config filtering
def config_key_filter_mode() -> str:
  return f"{config_key_prefix}filter_mode"

def config_get_filter_mode() -> str | None:
  return get_config(config_key_filter_mode()) or None

def config_key_filter_list() -> str:
  return f"{config_key_prefix}filter_list"

def config_get_filter_list() -> str | None:
  return get_config(config_key_filter_list()) or None

# default config
config_default = {
  config_key_no_bloods(): "3",
  
  config_key_blood_val(1): "20",
  config_key_blood_title(1): "First Blood",
  config_key_blood_icon(1): "lightning",
  
  config_key_blood_val(2): "10",
  config_key_blood_title(2): "Second Blood",
  config_key_blood_icon(2): "lightning",
  
  config_key_blood_val(3): "5",
  config_key_blood_title(3): "Third Blood",
  config_key_blood_icon(3): "lightning",
  
  config_key_filter_mode(): "blacklist",
  config_key_filter_list(): ""
}

def bloods_config_init():
  """sets config to the default if it doesn't exist yet"""

  # check if a config already exists
  if get_config(config_key_no_bloods()) is not None:
    return
  
  # set default config
  for key, val in config_default.items():
    set_config(key, val)

def bloods_config_reset():
  """resets the config to the default"""

  # erase existing config
  existing_config = Configs.query.filter(Configs.key.like(f"{config_key_prefix}%")).all()

  for config_row in existing_config:
    db.session.delete(config_row)
    
  db.session.commit()
  
  # set default config
  for key, val in config_default.items():
    set_config(key, val)


def bloods_remove_bloods_for_chal(chal):
  trackers = BloodAward.query.filter_by(challenge_id=chal.id).all()
  
  for tracker in trackers:
    award = Awards.query.filter_by(id=tracker.award_id).first()
    if award:
      db.session.delete(award)
      
    db.session.delete(tracker)
  
  db.session.commit()

def bloods_sync():
  """Strictly enforces that the awards perfectly match the current top solvers and configuration."""
  
  user_mode = get_config("user_mode")
  challenges = Challenges.query.all()

  # retrieve the current config
  no_bloods = int(get_config(config_key_no_bloods()) or 3)

  bloods_val = {}
  bloods_titles = {}
  bloods_icons = {}

  for i in range(1, no_bloods + 1):
      bloods_val[i] = int(get_config(config_key_blood_val(i)) or 0)

      default_title = (
          "First Blood"
          if i == 1
          else (
              "Second Blood"
              if i == 2
              else "Third Blood" if i == 3 else f"{i}th Blood"
          )
      )
      bloods_titles[i] = get_config(config_key_blood_title(i)) or default_title
      bloods_icons[i] = get_config(config_key_blood_icon(i)) or "lightning"

  filter_mode = config_get_filter_mode()
  filter_raw = config_get_filter_list()
  filter_list = [name.strip() for name in filter_raw.split(",") if name.strip()]
  if filter_list is None:
    raise ValueError(f"Invalid filter list")
    
  # sync bloods for each chal
  for chal in challenges:

    # check if this chal has bloods enabled
    has_blood: bool = False
    if filter_mode == "blacklist":
      has_blood = chal.name not in filter_list
    elif filter_mode == "whitelist":
      has_blood = chal.name in filter_list
    else:
      raise ValueError(f"Invalid filter mode: {filter_mode}")

    if not has_blood: # chal doesn't have bloods enabled
      # destroy any existing awards (if it has any)
      bloods_remove_bloods_for_chal(chal)
      
      continue

    # find the solutions for this chal
    query = Solves.query.join(Users, Solves.user_id == Users.id).filter(
      Solves.challenge_id == chal.id,
      Users.banned == False, # user must not be banned
      Users.hidden == False # user must not be hidden
    )

    if user_mode == "teams":
      query = query.join(Teams, Solves.team_id == Teams.id).filter(
        Teams.banned == False, # team must not be banned
        Teams.hidden == False # team must not be hidden
      )

    # find the top solutions (the first solutions)
    top_solves = (
      query.order_by(
        Solves.date.asc(),
        Solves.id.asc()
      ).limit(no_bloods).all()
    )
    top_solves = {i + 1: solve for i, solve in enumerate(top_solves)}

    valid_positions_kept = []

    # destroy or update existing awards
    trackers = BloodAward.query.filter_by(challenge_id=chal.id).all()
    for tracker in trackers:
      if tracker.position > no_bloods:
        award = Awards.query.filter_by(id=tracker.award_id).first()
        if award:
          db.session.delete(award)
        db.session.delete(tracker)
        continue

      expected_solve = top_solves.get(tracker.position)

      if (
        expected_solve
        and tracker.user_id == expected_solve.user_id
        and tracker.team_id == expected_solve.team_id
      ):
        award = Awards.query.filter_by(id=tracker.award_id).first()
        if award:
          award.name = bloods_titles[tracker.position]
          award.description = f"{chal.name}"
          award.value = bloods_val[tracker.position]
          award.icon = bloods_icons[tracker.position]
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
    for pos, solve in top_solves.items():
        if pos not in valid_positions_kept:
            award = Awards(
                user_id=solve.user_id,
                team_id=solve.team_id,
                name=bloods_titles[pos],
                description=f"{chal.name}",
                value=bloods_val[pos],
                icon=bloods_icons[pos],
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

  # on startup, init config config and perform initial sync
  with app.app_context():
    bloods_config_init()

    try:
      bloods_sync()
      print("[bloods plugin] initial sync completed successfully on startup!")
    except Exception as e:
      print(f"[bloods plugin] initial sync (during startup) error: {e}")

  # bloods blueprint
  bloods_bp = Blueprint("bloods", __name__, template_folder="templates")

  @bloods_bp.route("/admin/bloods", methods=["GET", "POST"])
  @admins_only
  def bloods_admin_dashboard():
    if request.method == "POST":
      action = request.form.get("action")
      
      # reset config
      if action == "reset":
        # reset the config
        bloods_config_reset()
        
        # perform sync
        bloods_sync()
        
        return render_template(
          "admin_bloods.html",
          success=True,
          message="config has been reset to default",
          no_bloods=int(get_config(config_key_no_bloods()) or 3),
        )
        
      # save config
      elif action == "save":
        # update the config
        for key in request.form:
          if key.startswith(f"{config_key_prefix}"):
            set_config(key, request.form[key])

        # perform sync
        bloods_sync()
        
        return render_template(
          "admin_bloods.html",
          success=True,
          message="config has beed updated",
          no_bloods=int(get_config(config_key_no_bloods()) or 3),
        )
      
      # invalid action
      else:
          return render_error()

    elif request.method == "GET":
      return render_template(
        "admin_bloods.html",
        no_bloods=int(get_config(config_key_no_bloods()) or 3),
      )
      
    else:
      return render_error()

  @bloods_bp.route("/bloods", methods=["GET"])
  def bloods_page():
    if request.method == "GET":
      bloods = []

      bloods_data = BloodAward.query.all()
      for blood_data in bloods_data:
        chal = Challenges.query.get(blood_data.challenge_id)
        if not chal:
          continue
        
        user = Users.query.get(blood_data.user_id)
        team = Teams.query.get(blood_data.team_id) if blood_data.team_id else None
        solve = Solves.query.filter_by(
            challenge_id=blood_data.challenge_id, user_id=blood_data.user_id
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
            "position": blood_data.position,
          }
        )
      
      bloods.sort(
          key=lambda x: x["date"] if x["date"] else datetime.datetime.min,
          reverse=True,
      )
      return render_template("bloods.html", bloods=bloods)

    else:
      return render_error()
    
  app.register_blueprint(bloods_bp)

  # register the admin dashboard in the admin menu bar
  register_admin_plugin_menu_bar("Bloods Config", "/admin/bloods")

  # register the page in the user menu bar
  register_user_page_menu_bar("Bloods", "/bloods")

  # perform sync on every action that could have caused a relevant change
  @app.after_request
  def trigger_sync(response):
    if request.method in ["POST", "PATCH", "DELETE"]:
      path = request.path
      if (
        path.startswith("/api/v1/challenges")
        or path.startswith("/api/v1/users")
        or path.startswith("/api/v1/teams")
        or path.startswith("/api/v1/solves")
        or path.startswith("/api/v1/submissions")
      ):
        try:
          bloods_sync()
        except Exception as e:
          print(f"[Bloods Plugin] sync error: {e}")
    
    return response
  