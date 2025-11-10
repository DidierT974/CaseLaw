import { useState, useEffect } from 'react'
import { supabase } from '../../lib/supabaseClient'
import { useRouter } from 'next/router'
import Link from 'next/link'
import dynamic from 'next/dynamic'

// Importer la timeline de manière dynamique
const DynamicChrono = dynamic(() => import('react-chrono').then(mod => mod.Chrono), { ssr: false })

export default function DossierDetail() {
  const router = useRouter()
  const { id: dossierId } = router.query

  const [dossier, setDossier] = useState(null)
  const [documents, setDocuments] = useState([])
  const [faits, setFaits] = useState([])
  const [timelineData, setTimelineData] = useState([])
  const [uploading, setUploading] = useState(false)
  const [loading, setLoading] = useState(true)

  // États pour le Chat RAG
  const [chatHistory, setChatHistory] = useState([]) 
  const [chatInput, setChatInput] = useState('')
  const [isChatLoading, setIsChatLoading] = useState(false)

  useEffect(() => {
    if (dossierId) {
      fetchData()
    }
  }, [dossierId])

  function formatFaitsForTimeline(faitsData) {
    if (!faitsData || faitsData.length === 0) return []
    return faitsData
      .filter(fait => fait.date_fait)
      .map(fait => ({
        title: fait.date_fait,
        cardTitle: fait.type_fait || "Événement",
        cardSubtitle: fait.acteurs || "Acteurs non spécifiés",
        cardDetailedText: fait.description,
      }))
  }

  async function fetchData() {
    setLoading(true)
    // 1. Dossier
    const { data: dossierData } = await supabase.from('Dossiers').select('nom').eq('id', dossierId).single()
    setDossier(dossierData)
    // 2. Documents
    const { data: documentsData } = await supabase.from('Documents').select('*').eq('dossier_id', dossierId).order('created_at', { ascending: false })
    setDocuments(documentsData || [])
    // 3. Faits
    const { data: faitsData } = await supabase.from('Faits').select('*').eq('dossier_id', dossierId).order('date_fait', { ascending: true, nullsFirst: false })
    setFaits(faitsData || [])
    setTimelineData(formatFaitsForTimeline(faitsData))
    setLoading(false)
  }

  async function handleUpload(event) {
    const file = event.target.files[0]
    if (!file || !dossierId) return
    setUploading(true)
    const filePath = `${dossierId}/${new Date().toISOString()}_${file.name}`

    // 1. Uploader vers Supabase Storage
    // NOTE: Nous devons configurer les permissions de Storage !
    const { error: uploadError } = await supabase.storage
      .from('documents') // Nom du bucket (à créer)
      .upload(filePath, file)

    if (uploadError) {
      alert('Erreur upload: ' + uploadError.message); setUploading(false); return;
    }

    // 2. Récupérer l'URL
    const { data: urlData } = supabase.storage
      .from('documents')
      .getPublicUrl(filePath)

    // 3. Insérer la référence dans la BDD
    const { error: dbError } = await supabase.from('Documents').insert({
        dossier_id: dossierId, nom: file.name, fichier_url: urlData.publicUrl, statut: 'A traiter'
    })
    if (dbError) alert('Erreur BDD: ' + dbError.message);
    else fetchData(); // Rafraîchir
    setUploading(false)
  }

  async function handleProcessDocument(documentId) {
    try {
      setDocuments(docs => docs.map(d => d.id === documentId ? { ...d, statut: 'En cours...' } : d))

      // Appeler notre backend IA (Phase 2)
      const response = await fetch('/api/process_document', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document_id: documentId })
      })

      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Erreur inconnue')

      alert(`Succès! ${result.faits_extraits} faits extraits. Le document est mémorisé.`)
      fetchData() // Rafraîchir tout
    } catch (error) {
      alert('Erreur lors du traitement: ' + error.message)
      fetchData()
    }
  }

  // Fonction pour gérer le Chat
  async function handleChatSubmit(event) {
    event.preventDefault()
    if (!chatInput || isChatLoading) return

    const userMessage = { role: 'user', content: chatInput }
    setChatHistory(prev => [...prev, userMessage])
    setIsChatLoading(true)
    setChatInput('')

    try {
      // Appeler notre backend Chat (Phase 2)
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: chatInput,
          dossier_id: dossierId
        })
      })

      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Erreur inconnue')

      const assistantMessage = { role: 'assistant', content: result.answer }
      setChatHistory(prev => [...prev, assistantMessage])

    } catch (error) {
      const errorMessage = { role: 'assistant', content: `Erreur: ${error.message}` }
      setChatHistory(prev => [...prev, errorMessage])
    }
    setIsChatLoading(false)
  }

  if (loading) return <div className="container"><p>Chargement...</p></div>

  return (
    <div className="container">
      <p><Link href="/">&larr; Retour aux dossiers</Link></p>
      <h1>{dossier?.nom}</h1>

      <div className="dossier-grid">
        {/* Colonne de Gauche: Ingestion et Chat */}
        <div>
          <div className="card">
            <h3>1. Uploader & Analyser</h3>
            <input type="file" accept=".pdf" onChange={handleUpload} disabled={uploading} />
            {uploading && <p>Upload en cours...</p>}

            <table>
              <thead><tr><th>Documents</th><th>Statut</th><th>Action</th></tr></thead>
              <tbody>
                {documents.map(doc => (
                  <tr key={doc.id}>
                    <td>{doc.nom}</td>
                    <td>{doc.statut}</td>
                    <td>
                      {doc.statut === 'A traiter' && (
                        <button onClick={() => handleProcessDocument(doc.id)}>Analyser</button>
                      )}
                      {doc.statut === 'En cours...' && '...'}
                      {doc.statut === 'Traité' && '✅'}
                      {doc.statut === 'Erreur' && '❌'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card" style={{marginTop: '20px'}}>
            <h3>2. Interroger le Dossier (Chat)</h3>
            <div className="chat-window">
              {chatHistory.map((msg, index) => (
                <div key={index} style={{textAlign: msg.role === 'user' ? 'right' : 'left', margin: '5px 0'}}>
                  <span style={{
                    padding: '8px 12px', borderRadius: '10px',
                    backgroundColor: msg.role === 'user' ? '#0070f3' : '#e0e0e0',
                    color: msg.role === 'user' ? 'white' : 'black',
                    display: 'inline-block', maxWidth: '80%'
                  }}>
                    {msg.content}
                  </span>
                </div>
              ))}
              {isChatLoading && <p style={{textAlign: 'left'}}><i>L'assistant réfléchit...</i></p>}
            </div>
            <form onSubmit={handleChatSubmit} style={{display: 'flex', gap: '10px'}}>
              <input
                type="text"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Poser une question sur les documents..."
              />
              <button type="submit" disabled={isChatLoading} style={{width: '100px'}}>Envoyer</button>
            </form>
          </div>
        </div>

        {/* Colonne de Droite: Timeline */}
        <div>
          <div className="card">
            <h3>3. Timeline des Faits</h3>
            <div style={{ width: "100%", height: "800px" }}>
              {timelineData.length > 0 ? (
                <DynamicChrono
                  items={timelineData}
                  mode="HORIZONTAL"
                  slideShow
                  cardHeight={400}
                  theme={{ primary: "#0070f3", secondary: "#f4f4f4", cardBgColor: "#ffffff", cardForeColor: "#333", titleColor: "#0070f3" }}
                />
              ) : (
                <p>Aucun fait daté n'a été extrait pour ce dossier.</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
