import azure.functions as func
import logging
import os
import json

# Defensively import requests to debug deployment issues
try:
    import requests
except ImportError as e:
    requests = None
    IMPORT_ERROR = str(e)
else:
    IMPORT_ERROR = None

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# --- Configuration ---
TENANT_ID = os.environ.get('TENANT_ID')
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
WORKSPACE_ID = os.environ.get('WORKSPACE_ID')
REPORT_ID = os.environ.get('REPORT_ID')

# Check if we have the basics before defining the rest
if requests:
    AUTHORITY_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]
    POWERBI_API_URL = "https://api.powerbi.com/v1.0/myorg"
else:
    AUTHORITY_URL = None
    SCOPE = []
    POWERBI_API_URL = None

def get_aad_token():
    if not requests:
        raise Exception(f"Server Configuration Error: {IMPORT_ERROR}")
        
    if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET]):
        raise Exception("Missing required environment variables (TENANT_ID, CLIENT_ID, CLIENT_SECRET).")

    data = {
        'grant_type': 'client_credentials',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'scope': ' '.join(SCOPE)
    }
    
    response = requests.post(AUTHORITY_URL, data=data)
    response.raise_for_status()
    return response.json().get('access_token')

def get_report_details(access_token, workspace_id, report_id=None):
    if not requests:
        raise Exception(f"Server Configuration Error: {IMPORT_ERROR}")

    headers = {'Authorization': f'Bearer {access_token}'}
    
    if not report_id:
        # Fetch all reports and pick the first one
        url = f"{POWERBI_API_URL}/groups/{workspace_id}/reports"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        reports = response.json().get('value', [])
        if not reports:
            raise Exception("No reports found in the workspace.")
        report = reports[0]
        report_id = report['id']
    else:
        url = f"{POWERBI_API_URL}/groups/{workspace_id}/reports/{report_id}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        report = response.json()

    return report

def generate_embed_token(access_token, workspace_id, report_id, dataset_id):
    if not requests:
        raise Exception(f"Server Configuration Error: {IMPORT_ERROR}")

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    # "App Owns Data" - GenerateToken for the report
    url = f"{POWERBI_API_URL}/groups/{workspace_id}/reports/{report_id}/GenerateToken"
    
    body = {
        "accessLevel": "View",
        "datasetId": dataset_id
    }
    
    response = requests.post(url, headers=headers, json=body)
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        # Try to return the detailed error message from PBI
        try:
            error_details = response.json()
            if 'error' in error_details:
                logging.error(f"Power BI Error: {error_details}")
                # Raise a cleaner exception with the PBI message
                raise Exception(f"Power BI API Error ({response.status_code}): {error_details.get('error', {}).get('message', 'Unknown error')}")
        except ValueError:
            pass # Invalid JSON
        raise e
            
    return response.json()

@app.route(route="getReports")
def getReports(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request to list reports.')
    
    if IMPORT_ERROR:
        return func.HttpResponse(
            json.dumps({'error': f"Dependency Missing: {IMPORT_ERROR}"}),
            mimetype="application/json",
            status_code=500
        )
    
    try:
        token = get_aad_token()
        headers = {'Authorization': f'Bearer {token}'}
        url = f"{POWERBI_API_URL}/groups/{WORKSPACE_ID}/reports"
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        reports = response.json().get('value', [])
        
        # Return simplified list of reports
        report_list = [{'id': r['id'], 'name': r['name']} for r in reports]
        
        return func.HttpResponse(
            json.dumps(report_list),
            mimetype="application/json",
            status_code=200
        )
    except Exception as e:
        logging.error(str(e))
        return func.HttpResponse(
            json.dumps({'error': str(e)}),
            mimetype="application/json",
            status_code=500
        )

@app.route(route="getEmbedInfo")
def getEmbedInfo(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')

    if IMPORT_ERROR:
        return func.HttpResponse(
            json.dumps({'error': f"Dependency Missing: {IMPORT_ERROR}"}),
            mimetype="application/json",
            status_code=500
        )

    try:
        token = get_aad_token()
        
        # Check if report_id was passed in query string, if so use it.
        # Otherwise fall back to environment variable, or auto-select.
        requested_report_id = req.params.get('reportId')
        target_report_id = requested_report_id if requested_report_id else REPORT_ID

        # 1. Get Report Details (We need Embed URL and Dataset ID)
        report = get_report_details(token, WORKSPACE_ID, target_report_id)
        
        current_report_id = report['id']
        embed_url = report['embedUrl']
        dataset_id = report['datasetId']
        report_name = report['name']
        
        # 2. Generate Embed Token
        embed_token_data = generate_embed_token(token, WORKSPACE_ID, current_report_id, dataset_id)
        embed_token = embed_token_data['token']
        # Expiration is in embed_token_data['expiration'] if needed
        
        return func.HttpResponse(
            json.dumps({
                'accessToken': embed_token,
                'embedUrl': embed_url,
                'reportId': current_report_id,
                'reportName': report_name,
                'expiration': embed_token_data.get('expiration')
            }),
            mimetype="application/json",
            status_code=200
        )
        
    except Exception as e:
        logging.error(str(e))
        return func.HttpResponse(
            json.dumps({'error': str(e)}),
            mimetype="application/json",
            status_code=500
        )
