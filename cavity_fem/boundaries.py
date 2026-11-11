"""External-boundary selection and source-CAD to generated-face mapping."""
from copy import deepcopy

from .config import Port


def assign_external_boundaries(geometry, cfg):
    """Keep user config in original CAD IDs; store resolved mesh IDs in geometry."""
    info=deepcopy(geometry)
    source_faces=info.get('source_faces', info['faces'])
    sources={face['id']:face for face in source_faces}
    remap='source_faces' in info
    antenna_sources={antenna.face for antenna in cfg.antennas}
    antenna_sources.update(port.get('source_face') for port in info.get('antenna_ports',[]) if port.get('source_face'))
    chosen=[(port,'explicit') for port in cfg.ports]
    assigned_sources={face for port in cfg.ports for face in port.faces}
    names=({port.name for port in cfg.ports}|{antenna.name for antenna in cfg.antennas}
           |{port['name'] for port in info.get('antenna_ports',[])})
    candidates={face['id'] for face in source_faces if face.get('artificial_cap',False)}
    excluded=set(cfg.excluded_artificial_cap_faces)
    invalid=excluded-candidates
    if invalid:
        raise ValueError('自動開口から除外する元面ID %s は、この解析領域の人工キャップではありません。'
                         % ', '.join(str(face) for face in sorted(invalid)))
    automatic_sources=set()
    if cfg.all_artificial_caps:
        for face_id in sorted(candidates-assigned_sources-antenna_sources-excluded):
            name='opening_face_%d'%face_id
            while name in names:name+='_'  # Never collide with an explicitly named port.
            names.add(name)
            chosen.append((Port(name=name,faces=[face_id],kind='radiation'),'artificial_cap_candidate'))
            automatic_sources.add(face_id)
    ports=[];used=set()
    for port,selection in chosen:
        if set(port.faces)&antenna_sources:
            raise ValueError('アンテナ取付面を別の外部境界にも割り当てることはできません。')
        resolved=[]
        for source_id in port.faces:
            if source_id not in sources:
                raise ValueError('外部境界の元面ID %d が解析領域にありません。'%source_id)
            matching=[face for face in info['faces'] if (face.get('source_face') if remap else face['id'])==source_id]
            valid=[face['id'] for face in matching if not face.get('internal',False)
                   and face.get('category') not in ('interface','terminal','antenna_metal')]
            if not valid:
                raise ValueError('元面ID %d は外部の開口面に対応しません。内部界面や金属端子は選択できません。'%source_id)
            resolved.extend(valid)
        if used.intersection(resolved):
            raise ValueError('同じ生成面が複数の外部境界に割り当てられています。')
        used.update(resolved)
        ports.append({**port.model_dump(),'faces':sorted(resolved),'source_faces':list(port.faces),'selection':selection,
                      'boundary_model':'first_order_local_outgoing_impedance','pml':False})
    coax_faces={face for port in info.get('antenna_ports',[]) for face in port['faces']}
    if used & coax_faces:
        raise ValueError('同軸終端と外部開口の境界が重複しています。')
    original_walls=set(info.get('wall_faces',[face['id'] for face in info['faces']
                       if not face.get('internal',False) and face['id'] not in coax_faces]))
    info['wall_faces']=sorted(original_walls-used)
    info['oxide_wall_faces']=sorted(set(info.get('oxide_wall_faces',original_walls))-used)
    info['external_ports']=ports
    info['external_boundary_selection']={
        'method':'unassigned_artificial_caps' if cfg.all_artificial_caps else 'explicit_only',
        'all_artificial_caps':cfg.all_artificial_caps,
        'source_face_ids':sorted({face for port in ports for face in port['source_faces']}),
        'automatic_source_face_ids':sorted(automatic_sources),
        'explicit_source_face_ids':sorted(assigned_sources),
        'excluded_artificial_cap_faces':sorted(excluded),
        'artificial_cap_candidates':sorted(candidates),
        'remaining_artificial_caps_pec':sorted(candidates-{face for port in ports for face in port['source_faces']}-antenna_sources),
        'all_physical_openings_identified':False,
        'note':'Explicit ports and antennas take priority. Automatic selection opens only unassigned, non-excluded artificial caps with a first-order local radiation boundary in vacuum. STEP does not identify every physical connection automatically.'}
    if cfg.analysis_mode=='all_openings_qc' and not ports and not info.get('antenna_ports'):
        raise ValueError('Qcを求める外部開口または同軸アンテナを1つ以上指定してください。人工キャップは形状一覧で確認できます。')
    return info
