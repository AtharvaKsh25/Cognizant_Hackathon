import os
import io
import json
import base64
import re
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel
from typing import Dict, Any, List
from PIL import Image
from fastapi import FastAPI, UploadFile, File, HTTPException
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
from pdf2image import convert_from_bytes
# --- NEW: MongoDB Dependencies ---
import motor.motor_asyncio
from bson import ObjectId

load_dotenv()
app = FastAPI(title="Intelligent Document Processor API")

# --- NEW: MongoDB Connection ---
MONGO_DETAILS = os.getenv("MONGO_DETAILS")
if not MONGO_DETAILS:
    raise ValueError("MONGO_DETAILS environment variable not set!")

client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_DETAILS)

db = client.loan_processing
verified_collection = db.get_collection("verified_documents")

llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash-latest", temperature=0)

# --- PROMPT ENGINEERING UPGRADE ---

# 1. More specific classification options
classification_prompt_template = """
You are an expert document classifier. Your task is to analyze the provided document image and identify its type.
Respond with only one of the following categories: 'Payslip', 'Tax Form', 'PAN Card', 'Aadhaar Card', 'Driving License', 'Bank Statement', 'Form 16', 'ITR', or 'Other'.
Do not add any other text or explanation.
"""

# 2. More detailed extraction prompts for each document type
extraction_prompts = {
    "Payslip": """
    You are an expert AI assistant. From the provided payslip image, extract: "Applicant Name", "Gross Income", "Net Pay", "Total Taxes", and "Pay Period End Date".
    For each field, provide the 'value' and a 'confidence' score.
    Provide your response as a single, valid JSON object with keys "extracted_data" and "analysis".
    """,
    "Tax Form": """
    You are an expert AI assistant. From the provided tax form image, extract: "Applicant Name", "Total Income", "Taxes Paid", and "Assessment Year".
    For each field, provide the 'value' and a 'confidence' score.
    Provide your response as a single, valid JSON object with keys "extracted_data" and "analysis".
    """,
    "PAN Card": """
    You are an expert AI assistant. From the provided PAN Card image, extract: "Name", "Father's Name", "Date of Birth", and "PAN Number".
    For each field, provide the 'value' and a 'confidence' score.
    Provide your response as a single, valid JSON object with keys "extracted_data" and "analysis".
    """,
    "Aadhaar Card": """
    You are an expert AI assistant. From the provided Aadhaar Card image, extract: "Name", "Date of Birth", "Address", "Gender", and "Aadhaar Number" (the 12-digit number).
    For each field, provide the 'value' and a 'confidence' score.
    Provide your response as a single, valid JSON object with keys "extracted_data" and "analysis".
    """,
    "Driving License": """
    You are an expert AI assistant. From the provided Driving License image, extract: "Name", "Date of Birth", "Address", and "DL No" (the license number).
    For each field, provide the 'value' and a 'confidence' score.
    Provide your response as a single, valid JSON object with keys "extracted_data" and "analysis".
    """,
    "Default": """
    You are an expert AI assistant. From the provided document image, extract any personally identifiable information (PII) and key financial figures you can find.
    For each field, provide the 'value' and a 'confidence' score.
    Provide your response as a single, valid JSON object with keys "extracted_data" and "analysis".
    """
}

cross_validation_prompt = """
You are a senior loan underwriter AI. You have been provided with extracted data from multiple documents for a single loan application.
Your task is to perform a final cross-validation check. Analyze all the data and identify any critical inconsistencies between the documents.
Specifically check for mismatches in "Applicant Name" and "Date of Birth".
Here is the data from all documents:
---
{summarized_data}
---
Provide a summary of your findings as a single, valid JSON object with two keys: "overall_summary" (a string) and "validation_passed" (a boolean).
The final output must be ONLY the JSON object, with no extra text or markdown.
"""

# 3. A more robust and clearer final summary prompt
final_summary_prompt = """
You are the lead AI underwriter. You have been given the complete data extracted from a loan application package.
Your task is to generate a final, comprehensive summary report.

Based on all the information provided below:
1.  **Write a concise, two-sentence overall summary** of the applicant's financial profile and the quality of their documentation. This summary MUST NOT be empty.
2.  **List the most important financial metrics** as a list of strings, formatted as "Metric Name: Value".
3.  **Consolidate all red flags** and inconsistencies into a single list of strings.
4.  **Provide a final recommendation**: 'Approve', 'Deny', or 'Manual Review Required'.

Here is all the data:
---
{complete_data}
---

Provide your response as a single, valid JSON object with four keys: "overall_summary", "key_financial_metrics", "consolidated_red_flags", and "final_recommendation".
The final output must be ONLY the JSON object.
"""


def pil_to_base64(image):
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_str}"

async def process_single_file(file_content: bytes, filename: str) -> dict:
    images_to_process = []
    if filename.endswith('.pdf'):
        images_to_process = convert_from_bytes(file_content)
    elif filename.endswith(('.png', '.jpg', '.jpeg')):
        images_to_process.append(Image.open(io.BytesIO(file_content)))
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {filename}")

    if not images_to_process:
         raise HTTPException(status_code=400, detail="Could not convert document to image.")

    # 1. Classify
    classification_message = HumanMessage(content=[{"type": "text", "text": classification_prompt_template}, {"type": "image_url", "image_url": pil_to_base64(images_to_process[0])}])
    doc_type = llm.invoke([classification_message]).content.strip()

    # 2. Extract
    extraction_prompt = extraction_prompts.get(doc_type, extraction_prompts["Default"])
    content_parts = [{"type": "text", "text": extraction_prompt}]
    for img in images_to_process:
        content_parts.append({"type": "image_url", "image_url": pil_to_base64(img)})
    
    message = HumanMessage(content=content_parts)
    response_json_string = llm.invoke([message]).content
    
    try:
        clean_response = response_json_string.replace("```json", "").replace("```", "").strip()
        final_result = json.loads(clean_response)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"AI returned a non-JSON response: {response_json_string}")

    # --- FIX: The document type from the AI might be different from our specific keys ---
    # We should add the *actual* type returned by the classifier to the result
    final_result['document_type'] = doc_type
    final_result['filename'] = filename
    return final_result

@app.post("/process-application/")
async def process_application(files: List[UploadFile] = File(...)):
    try:
        application_id = str(uuid.uuid4())
        application_results = []
        for file in files:
            file_content = await file.read()
            single_result = await process_single_file(file_content, file.filename.lower())
            application_results.append(single_result)
        
        summarized_data_for_ai = [{"filename": res.get('filename'), "document_type": res.get('document_type'), "data": res.get('extracted_data')} for res in application_results]
        
        cross_val_message = HumanMessage(content=cross_validation_prompt.format(summarized_data=json.dumps(summarized_data_for_ai, indent=2)))
        cross_val_response_str = llm.invoke([cross_val_message]).content
        
        try:
            json_match = re.search(r'\{.*\}', cross_val_response_str, re.DOTALL)
            cross_val_json = json.loads(json_match.group(0)) if json_match else {}
        except json.JSONDecodeError:
            cross_val_json = {"overall_summary": "AI cross-validation returned an invalid format.", "validation_passed": False}

        complete_data_for_summary = { 
            "individual_documents": application_results,
            "initial_cross_validation": cross_val_json
        }
        summary_message = HumanMessage(content=final_summary_prompt.format(complete_data=json.dumps(complete_data_for_summary, indent=2)))
        summary_response_str = llm.invoke([summary_message]).content

        try:
            json_match = re.search(r'\{.*\}', summary_response_str, re.DOTALL)
            summary_json = json.loads(json_match.group(0)) if json_match else {}
        except json.JSONDecodeError:
            summary_json = {"final_recommendation": "Error", "overall_summary": "AI failed to generate a final summary report."}

        return {
            "application_id": application_id,
            "individual_document_results": application_results,
            "cross_validation_report": cross_val_json,
            "final_summary_report": summary_json
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during application processing: {str(e)}")


class VerificationPayload(BaseModel):
    application_id: str
    filename: str
    original_ai_data: Dict[str, Any]
    verified_data: Dict[str, str]

@app.post("/save-verified-document/")
async def save_verified_document(payload: VerificationPayload):
    try:
        await verified_collection.update_many(
            {"application_id": payload.application_id, "filename": payload.filename, "is_active": True},
            {"$set": {"is_active": False, "end_date": datetime.now(timezone.utc)}}
        )
        
        new_document_record = {
            "application_id": payload.application_id,
            "filename": payload.filename,
            "ai_data": payload.original_ai_data.get("extracted_data", {}),
            "verified_data": payload.verified_data,
            "start_date": datetime.now(timezone.utc),
            "end_date": None,
            "is_active": True
        }
        
        result = await verified_collection.insert_one(new_document_record)
        return {"status": "success", "message": f"Verified data for {payload.filename} saved with ID {result.inserted_id}."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save data to MongoDB: {str(e)}")

@app.get("/get-report-data/")
async def get_report_data():
    try:
        cursor = verified_collection.find({"is_active": True})
        documents = await cursor.to_list(length=None)
        for doc in documents:
            doc["_id"] = str(doc["_id"])
        return documents
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch report data: {str(e)}")

@app.delete("/delete-all-data/")
async def delete_all_data():
    try:
        await verified_collection.delete_many({})
        return {"status": "success", "message": "All verified data has been deleted from the database."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete data: {str(e)}")

