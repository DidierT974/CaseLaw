import { useState, useEffect } from 'react'
import { supabase } from '../lib/supabaseClient'
import Link from 'next/link'
import { useRouter } from 'next/router'

export default function Home() {
  const [dossiers, setDossiers] = useState([])
  const [newDossierName, setNewDossierName] = useState('')
  const [newDossierType, setNewDossierType] = useState('Général') 
  const [loading, setLoading] = useState(true)
  const router = useRouter()

  useEffect(() => {
    fetchDossiers()
  }, [])

  async function fetchDossiers() {
    setLoading(true)
    const { data, error } = await supabase
      .from('Dossiers')
      .select('*')
      .order('created_at', { ascending: false })

    if (error) console.error('Erreur fetchDossiers:', error)
    else setDossiers(data)
    setLoading(false)
  }

  async function handleCreateDossier() {
    if (!newDossierName) return

    const { data, error } = await supabase
      .from('Dossiers')
      .insert({ 
        nom: newDossierName,
        type: newDossierType 
      })
      .select()

    if (error) {
      alert('Erreur: ' + error.message)
    } else if (data) {
      router.push(`/dossier/${data[0].id}`)
    }
  }

  return (
    <div className="container">
      <h1>Mon Back-Office Contentieux</h1>

      <div className="card">
        <h3>Créer un nouveau dossier</h3>
        <input
          type="text"
          placeholder="Nom du dossier (ex: Dupont c. Durand)"
          value={newDossierName}
          onChange={(e) => setNewDossierName(e.target.value)}
        />

        <label htmlFor="dossier-type" style={{marginTop: '10px', display: 'block'}}>
          Type de contentieux :
        </label>
        <select 
          id="dossier-type"
          value={newDossierType}
          onChange={(e) => setNewDossierType(e.target.value)}
        >
          <option value="Général">Général (Défaut)</option>
          <option value="Marché Public">Marché Public</option>
          {/* Ajoutez d'autres types ici */}
        </select>

        <button onClick={handleCreateDossier} style={{marginTop: '10px'}}>Créer</button>
      </div>

      <hr style={{margin: '20px 0'}} />

      <h2>Dossiers Existants</h2>
      {loading ? (
        <p>Chargement...</p>
      ) : (
        dossiers.map(dossier => (
          <div key={dossier.id} className="card" style={{marginBottom: '10px'}}>
            <h3>
              <Link href={`/dossier/${dossier.id}`}>
                {dossier.nom}
              </Link>
            </h3>
            <p style={{margin: 0, opacity: 0.7}}>Type: {dossier.type}</p> 
            <small>Créé le: {new Date(dossier.created_at).toLocaleDateString()}</small>
          </div>
        ))
      )}
    </div>
  )
}
