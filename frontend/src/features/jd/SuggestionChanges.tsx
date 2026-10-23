import type { ScreeningProfile } from './api'
import type { WindTunnelSuggestion } from './windTunnelApi'
import { displayValue, patchLabel } from './windTunnelView'

export default function SuggestionChanges({ patches, profile }: { patches: WindTunnelSuggestion['patches']; profile: ScreeningProfile | null }) {
  return <div className="suggestion-table-scroll"><table className="suggestion-changes-table"><caption className="sr-only">Exact suggested changes</caption>
    <thead><tr><th scope="col">Where</th><th scope="col">Previous</th><th scope="col">Proposed</th></tr></thead>
    <tbody>{patches.map((patch, index) => <tr key={index}><th scope="row">{patchLabel(patch, profile)}</th>
      <td><span className="suggestion-diff-label is-previous">Previous</span> <span>{displayValue(patch.expected)}</span></td>
      <td><span className="suggestion-diff-label is-proposed">Proposed</span> <span>{displayValue(patch.proposed)}</span></td></tr>)}</tbody>
  </table></div>
}
