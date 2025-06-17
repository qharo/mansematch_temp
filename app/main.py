# app/main.py
import json
from datetime import datetime
from typing import Optional, Dict, Any, Union, List
from pathlib import Path
from uuid import uuid4
import logging
import html
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# Ensure log level is set appropriately (e.g., DEBUG for development)
# You can set LOG_LEVEL in your .env file or environment
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(), # Default to INFO if not set
    format='%(asctime)s.%(msecs)03dZ [%(process)d:%(thread)d] %(levelname)-5s [%(name)s] %(module)s.%(funcName)s: %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S'
)
logger = logging.getLogger(__name__)

from . import database
from .database import connect_to_mongo, close_mongo_connection

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
BIG_FIVE_QUIZ_FILE = BASE_DIR / "big_five.json"
MAS_QUIZ_FILE = BASE_DIR / "mas.json"


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    logger.info("Application startup: connecting to MongoDB...")
    connect_to_mongo()
    yield
    logger.info("Application shutdown: closing MongoDB connection...")
    close_mongo_connection()

app = FastAPI(title="mansematch", lifespan=lifespan)

FAKE_USERS_DB = {
    "user1@example.com": {
        "hashed_password": "password123", "email": "user1@example.com", "id": "user1"
    },
    "user2@example.com": {
        "hashed_password": "password456", "email": "user2@example.com", "id": "user2"
    },
    "tanayagrawal@mansematch.com": {
        "hashed_password": "IAmTanay98", "email": "tanayagrawal@mansematch.com", "id": "tanay1"
    }
}

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals['html_escape'] = html.escape
templates.env.filters['json_dumps'] = json.dumps

BFI10_TRAIT_DESCRIPTIONS = {
    "Extraversion": "Reflects tendency to be sociable, assertive, and energetic vs. reserved and quiet.",
    "Agreeableness": "Reflects tendency to be compassionate, cooperative, and kind vs. antagonistic and critical.",
    "Conscientiousness": "Reflects tendency to be organized, dependable, and responsible vs. careless and impulsive.",
    "Neuroticism": "Reflects tendency to experience negative emotions, such as anxiety and sadness (Emotional Stability is the inverse).",
    "Openness": "Reflects tendency to be imaginative, curious, and open to new experiences vs. conventional and preferring routine."
}

def get_bfi10_interpretation_details(trait_name: str, score: Optional[float]) -> Dict[str, str]:
    level = "N/A"
    level_description = "Score not available or trait not applicable."
    if score is not None:
        if score < 2.5: level = "Low"
        elif score <= 3.5: level = "Average"
        else: level = "High"

        if trait_name == "Neuroticism":
            if level == "Low": level_description = "Indicates a tendency to be calm, emotionally stable, and resilient to stress."
            elif level == "Average": level_description = f"Indicates a moderate or balanced expression of typical {trait_name.lower()} characteristics."
            else: level_description = "Indicates a tendency to experience emotional fluctuations, anxiety, or moodiness more frequently."
        else:
            if level == "Low": level_description = f"Indicates a lower expression of typical {trait_name.lower()} characteristics."
            elif level == "Average": level_description = f"Indicates a moderate or balanced expression of typical {trait_name.lower()} characteristics."
            else: level_description = f"Indicates a higher expression of typical {trait_name.lower()} characteristics."
    else:
        level = "N/A"
        level_description = "This trait was not scored."

    general_description = BFI10_TRAIT_DESCRIPTIONS.get(trait_name, "General description not available.")
    return {
        "level": level,
        "level_specific_description": level_description,
        "general_trait_description": general_description
    }

MAS12_SUBSCALE_MAP = {
    "PP": "Power-Prestige", "RT": "Retention-Time", "D": "Distrust", "A": "Anxiety"
}
MAS12_SUBSCALE_FULL_NAMES = list(MAS12_SUBSCALE_MAP.values())

def get_mas12_interpretation_details(dimension_name: str, score: Optional[float]) -> str:
    if score is None: return "Score not available."
    level = "Low" if score < 2.5 else "Medium" if score <= 3.5 else "High"
    interpretation_map = {
        "Power-Prestige": {
            "Low": "Minimal view of money as a status symbol.",
            "Medium": "Views money as a moderate status symbol.",
            "High": "Strongly views money as a symbol of status and success."
        },
        "Retention-Time": {
            "Low": "Less focused on saving and long-term planning.",
            "Medium": "Moderate focus on saving and planning for the future.",
            "High": "Strong focus on saving and future financial planning."
        },
        "Distrust": {
            "Low": "Generally trusting in money matters and dealings with others.",
            "Medium": "Some caution regarding money and others' motives.",
            "High": "Significant suspicion or cynicism about money dealings and motives."
        },
        "Anxiety": {
            "Low": "Little worry or stress about financial matters.",
            "Medium": "Moderate worry or concern about finances.",
            "High": "Frequent and significant anxiety or stress concerning money."
        }
    }
    interp_text = interpretation_map.get(dimension_name, {}).get(level, "Interpretation not available.")
    return f"{level}: {interp_text}"

def calculate_mas12_scores(questions: List[Dict[str, Any]], user_answers_dict: Dict[str, Union[int, float]]) -> Dict[str, Optional[float]]:
    subscale_scores: Dict[str, List[float]] = {name: [] for name in MAS12_SUBSCALE_FULL_NAMES}
    for question in questions:
        q_id_str, key_short = str(question.get("id")), question.get("key")
        user_answer_val = user_answers_dict.get(q_id_str)
        if user_answer_val is None or key_short is None:
            logger.warning(f"MAS-12 QID {q_id_str}: missing answer/key. Ans: {user_answers_dict}")
            continue
        full_subscale_name = MAS12_SUBSCALE_MAP.get(key_short)
        if not full_subscale_name:
            logger.warning(f"MAS-12 QID {q_id_str}: unknown key '{key_short}'")
            continue
        try:
            answer_val = float(user_answer_val)
            if not (1 <= answer_val <= 5):
                logger.warning(f"MAS-12 QID {q_id_str}: ans {answer_val} out of 1-5 range.")
                continue
            subscale_scores[full_subscale_name].append(answer_val)
        except (ValueError, TypeError):
            logger.error(f"MAS-12 QID {q_id_str}: non-numeric ans '{user_answer_val}'")
            continue
    final_avg_scores: Dict[str, Optional[float]] = {}
    for name, scores_list in subscale_scores.items():
        final_avg_scores[name] = round(sum(scores_list) / len(scores_list), 2) if scores_list else None
        if not scores_list: logger.warning(f"MAS-12: No valid scores for subscale {name}")
    return final_avg_scores

class HtmxRedirectException(HTTPException):
    def __init__(self, redirect_url: str):
        super().__init__(status_code=200, detail="HTMX redirect", headers={"HX-Redirect": redirect_url})

@app.exception_handler(HtmxRedirectException)
async def htmx_redirect_exception_handler(request: Request, exc: HtmxRedirectException):
    return Response(content=exc.detail, status_code=exc.status_code, headers=exc.headers)

def load_quizzes_data() -> Dict[str, Any]:
    all_quizzes_list = []
    quiz_files = [BIG_FIVE_QUIZ_FILE, MAS_QUIZ_FILE]

    for file_path in quiz_files:
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                if "quizzes" in data and isinstance(data["quizzes"], list):
                    all_quizzes_list.extend(data["quizzes"])
                    logger.debug(f"Successfully loaded {len(data['quizzes'])} quiz(zes) from {file_path}")
                else:
                    logger.warning(f"File {file_path} does not contain a 'quizzes' list. Skipping.")
        except FileNotFoundError:
            logger.error(f"Quiz data file {file_path} not found.")
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding {file_path}: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred while loading {file_path}: {e}")

    if not all_quizzes_list:
        logger.warning("No quiz definitions loaded. Returning empty quiz list.")
        return {"quizzes": []}

    return {"quizzes": all_quizzes_list}

async def get_current_user_from_cookie(request: Request) -> Optional[Dict[str, Any]]:
    user_email = request.cookies.get("user_session")
    if user_email and user_email in FAKE_USERS_DB:
        return FAKE_USERS_DB[user_email]
    return None

async def get_current_user_or_htmx_redirect(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_from_cookie)
) -> Dict[str, Any]:
    if not current_user:
        logger.warning(f"User not authenticated. Path: {request.url.path}. HX-Request: {'hx-request' in request.headers}")
        if "hx-request" in request.headers:
            raise HtmxRedirectException(redirect_url=app.url_path_for("auth_page_route"))
        else:
            raise HTTPException(status_code=307, detail="Not authenticated", headers={"Location": app.url_path_for("auth_page_route")})
    return current_user

@app.middleware("http")
async def common_template_vars_middleware(request: Request, call_next):
    request.state.user = await get_current_user_from_cookie(request)
    request.state.current_year = datetime.now().year
    response = await call_next(request)
    return response

@app.get("/", response_class=HTMLResponse, name="homepage")
async def homepage_route(request: Request):
    logger.info(f"Homepage requested by user: {request.state.user.get('email') if request.state.user else 'Anonymous'}")
    return templates.TemplateResponse("index.html", {"request": request, "title": "Welcome - mansematch"})

@app.post("/subscribe", name="subscribe")
async def subscribe_email_route(request: Request, email: str = Form(...)):
    logger.info(f"New email subscription: {email}")
    return HTMLResponse(f"<p class='text-green-600 font-semibold'>Thank you for subscribing, {html.escape(email)}!</p>")

@app.get("/auth", response_class=HTMLResponse, name="auth_page_route")
async def auth_page_route(request: Request):
    if request.state.user:
        logger.info(f"User {request.state.user['email']} already authenticated, redirecting to dashboard.")
        return RedirectResponse(url=app.url_path_for("dashboard_page_route"), status_code=303)
    logger.info("Auth page requested.")
    return templates.TemplateResponse("auth.html", {"request": request, "title": "Sign In / Sign Up"})

@app.post("/login", name="login_route")
async def login_user_route(request: Request, email: str = Form(...), password: str = Form(...)):
    user = FAKE_USERS_DB.get(email)
    if not user or user["hashed_password"] != password:
        logger.warning(f"Login failed for email: {email}")
        return HTMLResponse("<p class='text-red-600'>Invalid email or password. Please try again.</p>", status_code=401)
    logger.info(f"User {email} logged in successfully.")
    response = HTMLResponse(content="<p>Login successful! Redirecting...</p>", status_code=200)
    response.set_cookie(key="user_session", value=user["email"], httponly=True, max_age=1800, samesite="Lax", secure=request.url.scheme == "https", path="/")
    redirect_url = app.url_path_for("dashboard_page_route")
    response.headers["HX-Redirect"] = redirect_url
    logger.debug(f"Login successful, HX-Redirecting to: {redirect_url}")
    return response

@app.post("/logout", name="logout_route")
async def logout_user_route(request: Request):
    user_email = request.state.user.get('email') if request.state.user else "Unknown"
    logger.info(f"User {user_email} initiating logout.")
    context = {"request": request, "title": "Welcome - mansematch", "user": None}
    html_content = templates.get_template("index.html").render(context)
    response = HTMLResponse(content=html_content)
    response.delete_cookie("user_session", httponly=True, samesite="Lax", secure=request.url.scheme == "https", path="/")
    response.headers["HX-Push-Url"] = app.url_path_for("homepage")
    return response

@app.get("/dashboard", response_class=HTMLResponse, name="dashboard_page_route")
async def dashboard_page_route(request: Request, current_user: Dict[str, Any] = Depends(get_current_user_or_htmx_redirect)):
    logger.info(f"Dashboard requested by user: {current_user['email']}")
    quizzes_definitions = load_quizzes_data().get("quizzes", [])
    user_reports = []
    if database.reports_collection is not None:
        try:
            report_cursor = database.reports_collection.find({"user_id": current_user["id"]}).sort("date_taken", -1)
            user_reports = list(report_cursor)
            logger.debug(f"User {current_user['email']} has {len(user_reports)} reports.")
        except Exception as e:
            logger.error(f"Error fetching reports for user {current_user['email']}: {e}")
    else:
        logger.warning("Reports collection N/A. Cannot fetch reports for dashboard.")
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "title": "Dashboard - mansematch", "user": current_user,
        "quizzes": quizzes_definitions, "reports": user_reports
    })

@app.get("/quiz/{quiz_id}", response_class=HTMLResponse, name="quiz_page_route")
async def quiz_page_route(request: Request, quiz_id: str, current_user: Dict[str, Any] = Depends(get_current_user_or_htmx_redirect)):
    logger.info(f"Quiz page for quiz_id: {quiz_id} by user: {current_user['email']}")
    all_quiz_definitions = load_quizzes_data()
    quiz_detail = next((q for q in all_quiz_definitions.get("quizzes", []) if q["id"] == quiz_id), None)
    if not quiz_detail:
        logger.warning(f"Quiz ID: {quiz_id} not found for user {current_user['email']}.")
        raise HTTPException(status_code=404, detail=f"Quiz ID: {quiz_id} not found.")
    quiz_detail.setdefault("questions", [])
    logger.debug(f"Quiz '{quiz_detail.get('title')}' ({len(quiz_detail['questions'])}Q) for template.")
    return templates.TemplateResponse("quiz_page.html", {
        "request": request, "title": f"Quiz: {html.escape(quiz_detail.get('title', 'Quiz'))}",
        "user": current_user, "quiz": quiz_detail
    })

def calculate_bfi10_scores(questions: List[Dict[str, Any]], user_answers_dict: Dict[str, Union[int, float]]) -> Dict[str, Optional[float]]:
    scores: Dict[str, List[float]] = {"E": [], "A": [], "C": [], "N": [], "O": []}
    trait_map = {
        "E": "Extraversion", "A": "Agreeableness", "C": "Conscientiousness",
        "N": "Neuroticism", "O": "Openness"
    }
    for question in questions:
        q_id_str, key = str(question.get("id")), question.get("key")
        user_answer_val = user_answers_dict.get(q_id_str)
        if user_answer_val is None or key is None:
            logger.warning(f"BFI-10 QID {q_id_str}: missing answer/key. Ans: {user_answers_dict}")
            continue
        try:
            answer_val = float(user_answer_val)
        except (ValueError, TypeError):
            logger.error(f"BFI-10 QID {q_id_str}: non-numeric ans '{user_answer_val}'")
            continue
        trait_code, is_reversed = key[0], key.endswith("_R")
        if trait_code not in scores:
            logger.warning(f"BFI-10 QID {q_id_str}: unknown trait '{trait_code}' in key '{key}'")
            continue
        scores[trait_code].append((6 - answer_val) if is_reversed else answer_val)
    final_scores: Dict[str, Optional[float]] = {}
    for trait_code, items_scores in scores.items():
        trait_name = trait_map.get(trait_code)
        if not trait_name: continue
        final_scores[trait_name] = round(sum(items_scores) / len(items_scores), 2) if items_scores else None
        if not items_scores: logger.warning(f"BFI-10: No valid scores for trait {trait_name}")
    return final_scores

@app.post("/quiz/{quiz_id}/submit", name="submit_quiz_route")
async def submit_quiz_route(request: Request, quiz_id: str, answers: str = Form(...), current_user: Dict[str, Any] = Depends(get_current_user_or_htmx_redirect)):
    logger.info(f"Quiz submission: {quiz_id} by user: {current_user['email']}")
    logger.debug(f"Raw answers JSON: {answers}") # Use debug to avoid logging PII in prod INFO
    if database.reports_collection is None:
        logger.error("DB N/A. Cannot save quiz submission.")
        raise HTTPException(status_code=503, detail="DB service unavailable.")
    all_quiz_definitions = load_quizzes_data()
    quiz_detail = next((q for q in all_quiz_definitions.get("quizzes", []) if q["id"] == quiz_id), None)
    if not quiz_detail:
        logger.error(f"Quiz ID {quiz_id} not found during submission by {current_user['email']}.")
        raise HTTPException(status_code=404, detail=f"Quiz ID {quiz_id} not found.")
    try:
        user_answers_dict: Dict[str, Any] = json.loads(answers)
        logger.debug(f"Parsed user answers: {user_answers_dict}")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid answers JSON from {current_user['email']} for {quiz_id}: {e}")
        raise HTTPException(status_code=400, detail="Invalid answers format.")

    quiz_questions = quiz_detail.get("questions", [])
    report_score: Union[str, Dict[str, Optional[float]]]
    report_type = "standard"

    if quiz_id == "bfi-10":
        report_type = "bfi-10"
        bfi_numeric_answers: Dict[str, Union[int, float]] = {}
        for q in quiz_questions:
            q_id_str = str(q.get("id"))
            ans_val = user_answers_dict.get(q_id_str)
            if ans_val is not None:
                try:
                    bfi_numeric_answers[q_id_str] = float(ans_val)
                except (ValueError, TypeError):
                    logger.warning(f"BFI-10 QID {q_id_str}: could not convert answer '{ans_val}' to float. Skipping.")
            else:
                logger.warning(f"BFI-10 QID {q_id_str}: answer not found in submission. Skipping.")

        report_score = calculate_bfi10_scores(quiz_questions, bfi_numeric_answers)
        logger.info(f"User {current_user['email']} BFI-10 scores: {report_score}")

    elif quiz_id == "mas-12":
        report_type = "mas-12"
        mas_numeric_answers: Dict[str, Union[int, float]] = {}
        for q in quiz_questions:
            q_id_str = str(q.get("id"))
            ans_val = user_answers_dict.get(q_id_str)
            if ans_val is not None:
                try:
                    mas_numeric_answers[q_id_str] = float(ans_val)
                except (ValueError, TypeError):
                     logger.warning(f"MAS-12 QID {q_id_str}: could not convert answer '{ans_val}' to float. Skipping.")
            else:
                logger.warning(f"MAS-12 QID {q_id_str}: answer not found in submission. Skipping.")
        report_score = calculate_mas12_scores(quiz_questions, mas_numeric_answers)
        logger.info(f"User {current_user['email']} MAS-12 scores: {report_score}")
    else:
        logger.warning(f"Quiz {quiz_id} submitted by {current_user['email']} has no specific scoring. Defaulting score.")
        report_score = "N/A - Scoring not implemented for this quiz type"
        report_type = quiz_id

    new_report_id = f"rep_{uuid4().hex[:10]}"
    new_report_doc = {
        "id": new_report_id, "user_id": current_user["id"], "quiz_id": quiz_id,
        "quiz_title": quiz_detail.get('title', 'Quiz'),
        "quiz_description": quiz_detail.get('description', 'No description.'),
        "score": report_score, "date_taken": datetime.utcnow(),
        "answers_submitted": user_answers_dict, "report_type": report_type
    }
    try:
        insert_result = database.reports_collection.insert_one(new_report_doc)
        logger.info(f"New report {new_report_id} (DB _id: {insert_result.inserted_id}) saved for {current_user['email']}.")
    except Exception as e:
        logger.error(f"Error saving report {new_report_id} to DB for {current_user['email']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save quiz results.")
    report_page_url = app.url_path_for("report_page_route", report_id=new_report_id)
    logger.info(f"Redirecting {current_user['email']} to report: {report_page_url}")
    return RedirectResponse(url=report_page_url, status_code=303)

@app.get("/healthz", status_code=200)
async def health_check_route(): return {"status": "ok"}

@app.get("/report/{report_id}", name="report_page_route")
async def report_page_route(request: Request, report_id: str, current_user: Dict[str, Any] = Depends(get_current_user_or_htmx_redirect)):
    logger.info(f"User {current_user['email']} requesting report: {report_id}")
    if database.reports_collection is None:
        logger.error(f"DB N/A for report {report_id}")
        raise HTTPException(status_code=503, detail="DB service unavailable.")
    try:
        report_detail = database.reports_collection.find_one({"id": report_id, "user_id": current_user["id"]})
    except Exception as e:
        logger.error(f"Error fetching report {report_id} from DB for {current_user['email']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load report.")
    if not report_detail:
        logger.warning(f"Report {report_id} not found or access denied for {current_user['email']}.")
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found/denied.")

    context = {
        "request": request, "title": f"Report: {html.escape(report_detail.get('quiz_title', 'Report'))}",
        "user": current_user, "report": report_detail
    }
    report_type, score_data = report_detail.get("report_type"), report_detail.get("score")

    # Log the type of score_data for diagnosis
    logger.debug(f"Report {report_id} - Report Type: {report_type}, Score Data Type: {type(score_data)}, Score Data: {str(score_data)[:200]}")


    if report_type == "bfi-10" and isinstance(score_data, dict):
        trait_scores: Dict[str, Optional[float]] = score_data
        radar_labels = ["Extraversion", "Agreeableness", "Conscientiousness", "Neuroticism", "Openness"]
        # Ensure data exists for all labels, defaulting to 0.0 if not found or if score_data[label] is None
        radar_data = [trait_scores.get(label, 0.0) if trait_scores.get(label) is not None else 0.0 for label in radar_labels]

        context["radar_chart_labels"], context["radar_chart_data"] = radar_labels, radar_data
        table_data = []
        for trait in radar_labels:
            score_val = trait_scores.get(trait) # Can be None if not in score_data
            interp = get_bfi10_interpretation_details(trait, score_val)
            table_data.append({
                "trait": trait, "score": score_val if score_val is not None else "N/A",
                "interpretation_level": interp["level"],
                "interpretation_description": interp["level_specific_description"],
                "general_trait_description": interp["general_trait_description"]
            })
        context["bfi_table_data"] = table_data
        logger.info(f"Rendering BFI-10 report template ('big_five_report.html') for report {report_id}")
        logger.debug(f"Context for BFI-10 report {report_id} (first 500 chars of radar_data): {str(context.get('radar_chart_data'))[:500]}")
        return templates.TemplateResponse("big_five_report.html", context)

    elif report_type == "mas-12" and isinstance(score_data, dict):
        subscale_scores: Dict[str, Optional[float]] = score_data
        pie_labels, pie_values = [], []
        for name in MAS12_SUBSCALE_FULL_NAMES: # Iterate in defined order
            score = subscale_scores.get(name) # Can be None
            if score is not None: # Only include if score exists
                pie_labels.append(name)
                pie_values.append(float(score)) # Ensure float
            else: # If a subscale score is missing, log it but don't add to chart
                logger.warning(f"MAS-12 report {report_id}: Missing score for subscale '{name}'. Not including in pie chart.")


        total_sum = sum(pie_values)
        if total_sum > 0:
            pie_percentages = [(v / total_sum) * 100 for v in pie_values]
        elif pie_values: # total_sum is 0 but there are values (all must be 0)
            pie_percentages = [0.0 for _ in pie_values] # Show as 0%
        else: # No valid pie_values (all subscales were None or list was empty)
            pie_percentages = []
            pie_labels = [] # Ensure labels are also empty if no data

        # Check for all_positive for Jinja if needed
        context["all_positive_pie_data"] = any(p > 0 for p in pie_percentages)


        context["pie_chart_labels"], context["pie_chart_data"] = pie_labels, pie_percentages
        mas_table_data = []
        for name in MAS12_SUBSCALE_FULL_NAMES:
            score_val = subscale_scores.get(name)
            mas_table_data.append({
                "dimension": name, "score": score_val if score_val is not None else "N/A",
                "interpretation": get_mas12_interpretation_details(name, score_val)
            })
        context["mas_table_data"] = mas_table_data
        logger.info(f"Rendering MAS-12 report template ('mas_report.html') for report {report_id}")
        logger.debug(f"Context for MAS-12 report {report_id} (pie_chart_data): {str(context.get('pie_chart_data'))}")
        return templates.TemplateResponse("mas_report.html", context)
    else:
        logger.warning(
            f"Rendering GENERIC report template ('report_page.html') for report {report_id}. "
            f"Report Type: {report_type}, Score Data Type: {type(score_data)}"
        )
        return templates.TemplateResponse("report_page.html", context)

if __name__ == "__main__":
    import uvicorn
    logger.info("Attempting to run Uvicorn for local development...")
    if os.getenv("MONGO_URI") and database.reports_collection is None and hasattr(app.router, 'lifespan_context') and app.router.lifespan_context:
         logger.warning("MongoDB URI set, but reports_collection is None. DB features might be unavailable.")
    elif not os.getenv("MONGO_URI"):
        logger.warning("MONGO_URI not set. MongoDB features will be disabled.")
    uvicorn.run(
        "app.main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", 8000)),
        reload=True, reload_dirs=[str(BASE_DIR.parent)], log_level=os.getenv("LOG_LEVEL", "info").lower()
    )
