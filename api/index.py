import os
import io
import json
import pypdf

# NOUVEAU: Imports pour Gemini
from google import genai
from google.genai import types 
from fastapi import FastAPI, HTTPException, Request
from supabase import create_client, Client
from google.oauth2 import service_account
from google.cloud import vision
from langchain.text_splitter import RecursiveCharacterTextSplitter

# --- INITIALISATION ---

app = FastAPI()

# Récupérer les clés secrètes
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # NOUVEAU
GOOGLE_JSON_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")

# Initialiser les clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY) # NOUVEAU
vision_client = None

# ... (Initialisation Vision client inchangée)
if GOOGLE_JSON_CREDENTIALS:
    try:
        credentials_info = json.loads(GOOGLE_JSON_CREDENTIALS)
        google_credentials = service_account.Credentials.from_service_account_info(credentials_info)
        vision_client = vision.ImageAnnotatorClient(credentials=google_credentials)
    except Exception as e:
        print(f"Erreur initialisation Google Vision: {e}")

# Initialiser le découpeur de texte
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000, 
    chunk_overlap=200, 
    length_function=len,
)

# --- DÉFINITION DES PROMPTS SPÉCIALISÉS (Inchangée) ---

PROMPT_GENERAL = """
Tu es un assistant juridique expert en contentieux. Analyse le texte suivant. Ta mission est d'extraire TOUS les faits et événements pertinents.
Réponds **uniquement** en format JSON, dans un tableau `{"faits": [...]}`. Chaque fait doit suivre cette structure exacte :

{
  "date_fait": "YYYY-MM-DD",
  "description": "Description concise de l'événement.",
  "acteurs": "Personne A, Société B",
  "type_fait": "Email / Réunion / Courrier / Notification"
}

Si une date est incertaine, utilise "null". N'invente rien. Extrais seulement.
"""

PROMPT_MARCHES_PUBLICS = """
Tu es un assistant juridique **spécialiste des marchés publics**. Analyse le texte suivant. Ta mission est d'extraire les faits clés spécifiques à ce contentieux.
Réponds **uniquement** en format JSON, dans un tableau `{"faits": [...]}`. La structure doit rester la même :

{
  "date_fait": "YYYY-MM-DD",
  "description": "Description spécifique (ex: Rejet de l'offre de [Société] pour motif [X], Publication de l'AAPC, Notification de la décision d'attribution)",
  "acteurs": "Pouvoir adjudicateur, Société candidate, Concurrent",
  "type_fait": "AAPC / Soumission / Négociation / Rejet / Attribution / Référé"
}

Concentre-toi sur les dates clés, les motifs de rejet, les parties prenantes et les étapes de la procédure.
"""

def get_prompt_for_dossier_type(dossier_type: str) -> str:
    """Retourne le Super-Prompt approprié."""
    if dossier_type == "Marché Public":
        print("Utilisation du prompt 'Marché Public'")
        return PROMPT_MARCHES_PUBLICS
    else:
        print("Utilisation du prompt 'Général'")
        return PROMPT_GENERAL

# --- FONCTION D'EXTRACTION DE TEXTE (OCR) (Inchangée) ---
def extract_text_from_pdf(pdf_content: bytes) -> str:
    texte_brut = ""
    try:
        with io.BytesIO(pdf_content) as f:
            reader = pypdf.PdfReader(f)
            for page in reader.pages:
                texte_brut += page.extract_text() + "\n\n"
    except Exception:
        texte_brut = ""
    if len(texte_brut.strip()) < 100 and vision_client:
        try:
            image = vision.Image(content=pdf_content)
            response = vision_client.document_text_detection(image=image)
            if response.full_text_annotation:
                texte_brut = response.full_text_annotation.text
        except Exception:
            pass 
    return texte_brut

# --- NOUVELLE FONCTION: CRÉER LES VECTEURS (Utilise GEMINI) ---
def create_embeddings_for_document(document_id: str, dossier_id: str, texte_brut: str):
    print(f"Début de la vectorisation (Gemini 768) pour {document_id}...")
    
    chunks = text_splitter.split_text(texte_brut)
    if not chunks: return 0
        
    chunks_to_insert = []
    for chunk_text in chunks:
        try:
            # NOUVEAU: Appel à l'API Gemini pour l'embedding
            response = gemini_client.models.embed_content(
                model="text-embedding-004", # Modèle d'embedding Gemini (768 dimensions)
                content=chunk_text,
                task_type="RETRIEVAL_DOCUMENT" # Type de tâche pour le RAG
            )
            embedding_vector = response['embedding'] # Récupération du vecteur
            
            chunks_to_insert.append({
                "document_id": document_id,
                "dossier_id": dossier_id,
                "content": chunk_text,
                "embedding": embedding_vector
            })
        except Exception as e:
            print(f"Erreur lors de la création d'un embedding Gemini: {e}")
            
    if chunks_to_insert:
        try:
            supabase.table("Document_Chunks").insert(chunks_to_insert).execute()
            return len(chunks_to_insert)
        except Exception as e:
            print(f"Erreur lors de l'insertion des chunks: {e}")
            
    return 0

# --- ENDPOINT /api/process_document (Extraction de faits) ---
@app.post("/api/process_document")
async def process_document(request: Request):
    try:
        data = await request.json()
        document_id = data.get("document_id")
        if not document_id: raise HTTPException(status_code=400, detail="document_id manquant")

        # 1. Statut
        supabase.table("Documents").update({"statut": "En cours"}).eq("id", document_id).execute()

        # 2. Récupérer les infos (y compris le type de dossier)
        doc_data = supabase.table("Documents").select("fichier_url, dossier_id, Dossiers(type)").eq("id", document_id).single().execute()
        fichier_url = doc_data.data["fichier_url"]
        dossier_id = doc_data.data["dossier_id"]
        dossier_type = doc_data.data.get("Dossiers", {}).get("type", "Général")
        
        # 3. Télécharger le fichier
        path_parts = fichier_url.split('/')
        bucket_name = path_parts[-2]
        file_path = path_parts[-1]
        storage_response = supabase.storage.from_(bucket_name).download(file_path)
        
        # 4. Extraire le texte (OCR ou non)
        texte_brut = extract_text_from_pdf(storage_response)
        if not texte_brut.strip():
            supabase.table("Documents").update({"statut": "Erreur - Fichier vide"}).eq("id", document_id).execute()
            return {"status": "erreur", "detail": "Fichier vide ou illisible"}
        
        # 5. Stocker le texte brut
        supabase.table("Documents").update({"texte_brut": texte_brut}).eq("id", document_id).execute()

        # 6. Sélectionner le bon Super-Prompt et générer le JSON de faits (Utilise GEMINI PRO)
        system_prompt = get_prompt_for_dossier_type(dossier_type)
        
        # NOUVEAU: Appel à l'API Gemini pour l'extraction (avec schéma JSON forcé)
        chat_completion = gemini_client.models.generate_content(
            model="gemini-2.5-pro", # Modèle de précision pour l'extraction
            contents=[
                {"role": "system", "parts": [{"text": system_prompt}]},
                {"role": "user", "parts": [{"text": f"Voici le texte à analyser : \n\n{texte_brut}"}]}
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                # Schéma pour garantir que le JSON est bien formaté pour la BDD
                response_schema=types.Schema(
                    type=types.Type.OBJECT,
                    properties={"faits": types.Schema(type=types.Type.ARRAY, items=types.Schema(
                        type=types.Type.OBJECT, 
                        properties={
                            "date_fait": types.Schema(type=types.Type.STRING), 
                            "description": types.Schema(type=types.Type.STRING),
                            "acteurs": types.Schema(type=types.Type.STRING),
                            "type_fait": types.Schema(type=types.Type.STRING)
                        }
                    ))}
                )
            )
        )
        
        # 7. Insérer les FAITS
        response_json = json.loads(chat_completion.text) # Gemini retourne le JSON dans l'attribut .text
        faits_extraits = response_json.get("faits", [])
        if faits_extraits:
            faits_a_inserer = [
                {**fait, "dossier_id": dossier_id, "document_id": document_id} 
                for fait in faits_extraits
            ]
            supabase.table("Faits").insert(faits_a_inserer).execute()

        # 8. LANCER LA VECTORISATION (RAG)
        create_embeddings_for_document(document_id, dossier_id, texte_brut)

        # 9. Marquer comme "Traité"
        supabase.table("Documents").update({"statut": "Traité"}).eq("id", document_id).execute()

        return {"status": "succès", "faits_extraits": len(faits_extraits)}

    except Exception as e:
        if 'document_id' in locals():
            supabase.table("Documents").update({"statut": "Erreur"}).eq("id", document_id).execute()
        raise HTTPException(status_code=500, detail=f"Erreur Backend: {e}")

# --- ENDPOINT /api/chat (Chat RAG) ---
@app.post("/api/chat")
async def chat_with_dossier(request: Request):
    try:
        data = await request.json()
        question = data.get("question")
        dossier_id = data.get("dossier_id")
        if not question or not dossier_id:
            raise HTTPException(status_code=400, detail="Question ou dossier_id manquant")

        # 1. Vectoriser la question (Utilise GEMINI)
        response = gemini_client.models.embed_content(
            model="text-embedding-004", 
            content=question,
            task_type="RETRIEVAL_QUERY"
        )
        query_embedding = response['embedding']

        # 2. Chercher les morceaux (RAG)
        context_chunks = supabase.rpc("match_document_chunks", {
            "query_embedding": query_embedding, "match_dossier_id": dossier_id,
            "match_count": 5, "match_threshold": 0.5
        }).execute()

        context_text = "\n\n---\n\n".join([chunk["content"] for chunk in context_chunks.data]) if context_chunks.data else "Aucune information pertinente trouvée."

        # 3. Construire le prompt de chat
        system_prompt = f"""
Tu es un assistant juridique. Ta mission est de répondre à la question de l'avocat en te basant **uniquement** sur le contexte suivant, extrait des documents du dossier.
Si le contexte ne contient pas la réponse, dis "Je ne trouve pas cette information dans les documents."

CONTEXTE :
{context_text}
"""
        
        # 4. Appeler l'IA pour la Génération (Utilise GEMINI FLASH)
        chat_completion = gemini_client.models.generate_content(
            model="gemini-2.5-flash", # Modèle rapide pour le chat
            contents=[
                {"role": "system", "parts": [{"text": system_prompt}]},
                {"role": "user", "parts": [{"text": question}]}
            ]
        )
        
        answer = chat_completion.text
        return {"answer": answer}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur Chat Backend: {e}")
