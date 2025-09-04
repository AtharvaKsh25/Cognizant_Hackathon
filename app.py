import streamlit as st
import requests
import json
import pandas as pd
import re
import os
import uuid

# --- Page Configuration ---
st.set_page_config(
    page_title="Intelligent Document Processor 🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Custom CSS for Styling ---
# WARNING: Targeting Streamlit's internal class names (like .st-emotion-cache-*) is brittle
# and may break with future Streamlit updates.
st.markdown(
    """
    <style>
    .big-font {
        font-size:30px !important;
        font-weight: bold;
    }
    .medium-font {
        font-size:22px !important;
        font-weight: bold;
        color: #1E88E5; /* A professional blue */
    }
    /* Targets the main block for the uploader */
    .st-emotion-cache-1r6dm7m {
        padding: 1rem;
        border-radius: 10px;
        border: 1px dashed #1E88E5;
    }
    /* Style for metric labels */
    .st-emotion-cache-1g6goon {
        font-size: 16px;
        color: #4A4A4A;
    }
    .report-box {
        border: 1px solid #e0e0e0;
        border-radius: 10px;
        padding: 20px;
        background-color: #fafafa;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# --- Initialize session state variables ---
if 'processing' not in st.session_state:
    st.session_state.processing = False
if 'application_results' not in st.session_state:
    st.session_state.application_results = None

# --- Sidebar for Navigation ---
st.sidebar.title("Navigation")
page = st.sidebar.radio(
    "Go to",
    ["Loan Application Processor", "Reporting Dashboard"],
    key="nav_radio"
)
st.sidebar.markdown("---")
st.sidebar.info("This project leverages Generative AI to automate loan document processing, including a human-in-the-loop verification workflow.")

# --- Helper function for the verification form ---
def display_verification_form(doc_data, application_id, unique_key):
    extracted_data = doc_data.get("extracted_data", {})
    filename = doc_data.get("filename", "unknown_file")

    if not extracted_data:
        st.warning("No structured data was extracted to verify.")
        return

    with st.form(key=f"verification_form_{unique_key}"):
        corrected_data = {}
        for field, details in extracted_data.items():
            value = details.get('value', '') if isinstance(details, dict) else details
            confidence = details.get('confidence', 0.0) * 100 if isinstance(details, dict) else 0.0

            help_text = f"Confidence: {confidence:.1f}%"
            if confidence < 75:
                help_text = f"⚠️ Low Confidence ({confidence:.1f}%) - Please verify carefully."

            corrected_data[field] = st.text_input(
                label=f"{field}",
                value=value,
                key=f"{unique_key}_{field.lower().replace(' ', '_')}",
                help=help_text
            )

        submitted = st.form_submit_button("Approve & Save This Document's Data")

        if submitted:
            with st.spinner("Saving verified data..."):
                payload = {
                    "application_id": application_id,
                    "filename": filename,
                    "original_ai_data": doc_data,
                    "verified_data": corrected_data
                }
                try:
                    save_response = requests.post("http://127.0.0.1:8000/save-verified-document/", json=payload)
                    if save_response.status_code == 200:
                        st.success(f"✅ Verified data for `{filename}` saved successfully!")
                    else:
                        st.error(f"Failed to save data for `{filename}`: {save_response.text}")
                except requests.exceptions.ConnectionError:
                    st.error("🚫 Connection Error: Could not connect to the backend to save data.")

# --- Page 1: Loan Application Processor ---
if page == "Loan Application Processor":
    st.title("🏦 Intelligent Loan Application Processor")
    st.markdown("---")
    st.markdown(
        """
        Upload all documents for a single loan application (e.g., Payslip, Tax Form, ID card).
        The AI will process each file, then generate a final underwriting report.
        """
    )
    st.markdown("---")

    st.header("📁 Upload Loan Application Package")
    col1_upload, col2_upload, col3_upload = st.columns(3)
    with col1_upload:
        st.subheader("1. KYC Documents")
        kyc_files = st.file_uploader("Upload PAN, Aadhaar, etc.", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="kyc_uploader")
    with col2_upload:
        st.subheader("2. Bank Statements")
        bank_files = st.file_uploader("Upload latest 6-month statements.", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="bank_uploader")
    with col3_upload:
        st.subheader("3. Income Proof")
        income_files = st.file_uploader("Upload Payslips, Form 16, ITR.", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="income_uploader")

    uploaded_files = kyc_files + bank_files + income_files

    if uploaded_files:
        st.markdown("---")
        if st.button("Process Full Application", type="primary", disabled=st.session_state.processing):
            st.session_state.processing = True
            st.info(f"✨ Processing {len(uploaded_files)} documents...")
            with st.spinner('AI is analyzing the application... This may take some time.'):
                multipart_files = [('files', (file.name, file.getvalue(), file.type)) for file in uploaded_files]
                try:
                    response = requests.post("http://127.0.0.1:8000/process-application/", files=multipart_files)
                    if response.status_code == 200:
                        st.success('✅ Application processed successfully!')
                        st.session_state.application_results = response.json()
                    else:
                        try:
                            error_detail = response.json().get('detail', response.text)
                        except json.JSONDecodeError:
                            error_detail = response.text
                        st.error(f"❌ Error from server ({response.status_code}): {error_detail}")
                        st.session_state.application_results = None
                except requests.exceptions.ConnectionError:
                    st.error("🚫 Connection Error: Could not connect to the backend.")
                    st.session_state.application_results = None
            st.session_state.processing = False
            st.rerun() # Rerun to update the button state

    if st.session_state.application_results:
        results = st.session_state.application_results
        application_id = results.get("application_id")

        st.markdown("---")
        st.header("🏁 Final Underwriting Summary Report")
        st.info(f"**Application ID:** `{application_id}` (Use this ID for tracking)")


        report = results.get('final_summary_report', {})
        recommendation = report.get('final_recommendation', 'Error')
        summary = report.get('overall_summary', "No summary provided.")
        metrics = report.get('key_financial_metrics', [])
        red_flags = report.get('consolidated_red_flags', [])

        if recommendation == 'Approve':
            st.success(f"**Recommendation: {recommendation}**")
        elif recommendation == 'Manual Review Required':
            st.warning(f"**Recommendation: {recommendation}**")
        else:
            st.error(f"**Recommendation: {recommendation}**")

        st.markdown(f"**AI Summary:** *{summary}*")
        st.markdown("---")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Key Financial Metrics")
            if metrics:
                for metric_str in metrics:
                    st.write(f"- {metric_str}")
            else:
                st.write("None identified.")

        with col2:
            st.markdown("##### Consolidated Red Flags")
            if red_flags:
                for flag in red_flags:
                    st.write(f"- 🚩 {flag}")
            else:
                st.write("None identified.")

        with st.expander("View Initial Cross-Validation Check"):
            cross_val_report = results.get('cross_validation_report', {})
            st.json(cross_val_report)

        st.markdown("---")
        st.subheader("📄 Individual Document Verification")
        st.info("Review each document below. You can correct the data and save it individually.")

        for i, doc_result in enumerate(results.get('individual_document_results', [])):
            doc_type = doc_result.get('document_type', 'Unknown')
            filename = doc_result.get('filename', 'N/A')

            with st.expander(f"**{doc_type}**: `{filename}`"):
                col1_doc, col2_doc = st.columns(2)
                with col1_doc:
                    display_verification_form(doc_result, application_id, unique_key=f"doc_{i}")
                with col2_doc:
                    st.markdown("##### AI Analysis")
                    analysis = doc_result.get('analysis', {})
                    if analysis:
                        st.json(analysis)
                    else:
                        st.write("No analysis provided.")

                # --- FIX: Replaced nested expander with a checkbox ---
                if st.checkbox("View Full Raw Data (for debugging)", key=f"raw_data_checkbox_{i}"):
                    st.json(doc_result)

# --- Page 2: Reporting Dashboard (Refactored for MongoDB) ---
elif page == "Reporting Dashboard":
    st.title("📊 Reporting Dashboard")
    st.markdown("---")

    try:
        # Fetches data from the new MongoDB endpoint
        response = requests.get("http://127.0.0.1:8000/get-report-data/")
        if response.status_code == 200:
            data = response.json()
            if data:
                records = []
                for item in data:
                    # --- CHANGE: Add 'is_active' status to the record ---
                    flat_record = {
                        "is_active": item.get("is_active"),
                        "application_id": item["application_id"],
                        "filename": item["filename"]
                    }

                    # Safely access ai_data and verified_data
                    for key, val in item.get("ai_data", {}).items():
                        flat_record[f"ai_{key.replace(' ', '_').lower()}"] = val.get("value") if isinstance(val, dict) else val
                    for key, val in item.get("verified_data", {}).items():
                        flat_record[f"verified_{key.replace(' ', '_').lower()}"] = val
                    records.append(flat_record)

                df = pd.DataFrame(records)
                
                # --- CHANGE: Only calculate metrics on active documents ---
                active_df = df[df['is_active'] == True]

                st.subheader("Key Performance Indicators (Based on Active Documents)")
                total_fields, matching_fields = 0, 0
                
                verified_cols = [col for col in active_df.columns if col.startswith('verified_')]
                fields_to_check = [col.replace('verified_', '') for col in verified_cols]

                for field in fields_to_check:
                    ai_col, verified_col = f"ai_{field}", f"verified_{field}"
                    if ai_col in active_df.columns and verified_col in active_df.columns:
                        df_subset = active_df.dropna(subset=[ai_col, verified_col]).copy()
                        
                        # --- FIX: Explicitly convert columns to string before comparison ---
                        df_subset[ai_col] = df_subset[ai_col].astype(str)
                        df_subset[verified_col] = df_subset[verified_col].astype(str)

                        comparison = df_subset[ai_col].str.strip().str.lower() == df_subset[verified_col].str.strip().str.lower()
                        matching_fields += comparison.sum()
                        total_fields += len(df_subset)

                ai_accuracy = (matching_fields / total_fields) * 100 if total_fields > 0 else 0
                total_docs = len(active_df)

                avg_income_col = next((col for col in ['verified_gross_income', 'verified_total_income'] if col in active_df.columns), None)
                avg_income = pd.to_numeric(active_df[avg_income_col], errors='coerce').mean() if avg_income_col else None
                
                avg_taxes_col = next((col for col in ['verified_total_taxes', 'verified_taxes_paid'] if col in active_df.columns), None)
                avg_taxes = pd.to_numeric(active_df[avg_taxes_col], errors='coerce').mean() if avg_taxes_col else None

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Active Docs", f"{total_docs}")
                col2.metric("Avg. Gross Income", f"₹{avg_income:,.2f}" if avg_income else "N/A")
                col3.metric("Avg. Total Taxes", f"₹{avg_taxes:,.2f}" if avg_taxes else "N/A")
                col4.metric("AI Accuracy", f"{ai_accuracy:.2f}%")

                st.markdown("---")
                st.subheader("Complete Data History")
                st.info("This table shows all verified documents, including older, inactive versions.")
                st.dataframe(df)

                st.markdown("---")
                st.subheader("Manage Data")
                if st.checkbox("I want to permanently delete all verified data."):
                    if st.button("Delete All Data", type="primary", help="This action cannot be undone."):
                        try:
                            # Calls the correct delete endpoint
                            delete_response = requests.delete("http://127.0.0.1:8000/delete-all-data/")
                            if delete_response.status_code == 200:
                                st.success("All verified data has been deleted successfully.")
                                st.rerun()
                            else:
                                st.error(f"Failed to delete data: {delete_response.text}")
                        except requests.exceptions.ConnectionError:
                            st.error("🚫 Connection Error.")
            else:
                st.warning("No verified data found in the database.")
        else:
            st.error(f"Failed to fetch report data from the backend: {response.text}")
    except requests.exceptions.ConnectionError:
        st.error("🚫 Connection Error: Could not connect to the backend.")
    except Exception as e:
        st.error(f"An unexpected error occurred while loading the dashboard: {e}")