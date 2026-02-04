import pdfplumber
import json
import os
import re

def limpiar_espacios(texto):
    if not texto: return ""
    return ' '.join(str(texto).split()).strip()

def es_basura_o_legal(texto_fila):
    """
    Detector de zonas prohibidas. Si aparece esto, ignoramos la línea.
    """
    texto = texto_fila.lower()
    
    palabras_prohibidas = [
        # Fin de la tabla principal
        "mitigación de riesgos", "seguridad del aplicador", "gestión de envases",
        "primeros auxilios", "almacenamiento", "eliminación del producto",
        "no contaminar", "clase y categoría", "indicaciones de peligro",
        "consejos de prudencia", "zona de protección", "banda de seguridad",
        "conforme a lo establecido", "real decreto", "orden jus",
        "uso profesional", "no autorizado", "euh401", "spp", "spe3", "spe8",
        
        # TABLAS QUE NO QUEREMOS (Plazos de seguridad y Pictogramas)
        "plazos de seguridad", "protección del consumidor", 
        "pictograma", "palabra de advertencia", "ghs0", "atención", "peligro",
        
        # Encabezados y pies de página
        "secretaria general", "dirección general", "producción agroalimentaria",
        "página", "hoja", "de 5", "de 4", "gobierno de españa", "ministerio de agricultura"
    ]

    if any(p in texto for p in palabras_prohibidas):
        return True
    return False

def es_dosis_valida(texto):
    """
    Verifica que la dosis sea un número, un porcentaje o una indicación estándar.
    Rechaza si la dosis parece un nombre de cultivo (ej: 'Tomate').
    """
    texto = texto.lower().strip()
    if not texto: return False
    
    # Si contiene palabras de cultivos comunes en la columna DOSIS, es la tabla de Plazos (ERROR)
    cultivos_falsos = ['tomate', 'pimiento', 'pepino', 'melón', 'sandía', 'ciruelo', 'frutal', 'cerez']
    if any(c in texto for c in cultivos_falsos):
        return False

    # Debe tener números O ser "consultar etiqueta"
    tiene_numero = bool(re.search(r'\d', texto))
    es_texto_valido = any(x in texto for x in ['consultar', 'etiqueta', 'na', 'n/a'])
    
    return (tiene_numero or es_texto_valido) and len(texto) < 60

def detectar_filas_graficas(pagina):
    """Detecta las líneas horizontales para cortar la tabla en rebanadas."""
    objetos = pagina.lines + pagina.rects
    cortes_y = []
    
    for obj in objetos:
        if obj['width'] > 100: 
            cortes_y.append(obj['top'])
            if obj['height'] > 1 and obj['height'] < 10:
                cortes_y.append(obj['bottom'])

    cortes_y = sorted(list(set([round(y) for y in cortes_y])))
    
    filas_limpias = []
    if cortes_y:
        filas_limpias.append(cortes_y[0])
        for y in cortes_y[1:]:
            if y - filas_limpias[-1] > 8: 
                filas_limpias.append(y)
    return filas_limpias

def extraer_tabla_v7(ruta_pdf):
    datos = []
    
    cols = {
        'uso': (0, 110),
        'agente': (110, 265),
        'dosis': (265, 350),
        'n_aplic': (350, 405),
        'intervalo': (405, 485),
        'vol_caldo': (485, 600),
        'condiciones': (600, 1000)
    }

    try:
        with pdfplumber.open(ruta_pdf) as pdf:
            uso_actual = ""
            tabla_encontrada = False
            fin_de_tabla_detectado = False

            for num_pag, pagina in enumerate(pdf.pages):
                if fin_de_tabla_detectado: break # Si ya cerramos la tabla, no leer más páginas

                cortes = detectar_filas_graficas(pagina)
                palabras_pag = pagina.extract_words(x_tolerance=2, y_tolerance=2)
                
                # Si no hay líneas, ignoramos (a menos que estemos en medio de la tabla)
                if len(cortes) < 2: continue

                for i in range(len(cortes) - 1):
                    y_top = cortes[i]
                    y_bottom = cortes[i+1]
                    
                    palabras_fila = [w for w in palabras_pag if y_top <= (w['top'] + (w['bottom']-w['top'])/2) < y_bottom]
                    if not palabras_fila: continue

                    # Construir columnas
                    texto_cols = {c: [] for c in cols}
                    for w in palabras_fila:
                        for col_nom, (xmin, xmax) in cols.items():
                            if xmin <= w['x0'] < xmax:
                                texto_cols[col_nom].append(w['text'])
                                break
                    
                    fila = {k: " ".join(v).strip() for k, v in texto_cols.items()}
                    texto_completo = " ".join(fila.values()).lower()

                    # --- DETECCIÓN DE PARADA (STOP) ---
                    # Si leemos "Plazos de seguridad", se acabó la tabla buena.
                    if "plazos de seguridad" in texto_completo or "protección del consumidor" in texto_completo:
                        fin_de_tabla_detectado = True
                        break

                    # --- FILTROS ---
                    # 1. Cabecera (Start)
                    if "uso" in fila['uso'].lower() and "agente" in fila['agente'].lower():
                        tabla_encontrada = True
                        continue
                    
                    if not tabla_encontrada: continue

                    # 2. Basura Legal / Headers / Footers
                    if es_basura_o_legal(texto_completo):
                        continue

                    # 3. Filtro específico de Dosis Falsa (El problema de "Tomate" en dosis)
                    if not es_dosis_valida(fila['dosis']):
                        # Si la dosis está vacía, puede ser continuación, PERO si tiene texto
                        # y ese texto no es una dosis (ej: "Tomate"), es basura.
                        if len(fila['dosis']) > 2 and "consultar" not in fila['dosis'].lower():
                            continue

                    # --- PROCESAMIENTO ---
                    if len(fila['uso']) > 2:
                        uso_actual = fila['uso']
                    
                    tiene_datos = len(fila['agente']) > 1 or len(fila['dosis']) > 0
                    
                    if tiene_datos:
                        # Limpieza extra de agente
                        if "pictograma" in fila['agente'].lower(): continue

                        lista_agentes = [fila['agente']]
                        if "," in fila['agente'] and len(fila['agente']) < 50:
                             lista_agentes = [x.strip() for x in fila['agente'].split(',')]

                        for ag in lista_agentes:
                            if not ag: continue
                            
                            datos.append({
                                "uso": uso_actual,
                                "agente": ag,
                                "dosis": fila['dosis'],
                                "n_aplic": fila['n_aplic'],
                                "intervalo": fila['intervalo'],
                                "vol_caldo": fila['vol_caldo'],
                                "condiciones": fila['condiciones'],
                                "archivo": os.path.basename(ruta_pdf)
                            })

            # Post-Proceso para rellenar dosis vacías (Herencia hacia abajo)
            # Solo si estamos seguros de que es el mismo bloque
            for i in range(len(datos)):
                if datos[i]['agente'] and not datos[i]['dosis']:
                     if i + 1 < len(datos):
                        vecino = datos[i+1]
                        if vecino['uso'] == datos[i]['uso'] and vecino['dosis']:
                            datos[i]['dosis'] = vecino['dosis']
                            datos[i]['n_aplic'] = vecino['n_aplic']
                            datos[i]['intervalo'] = vecino['intervalo']
                            datos[i]['vol_caldo'] = vecino['vol_caldo']
                            if not datos[i]['condiciones']:
                                datos[i]['condiciones'] = vecino['condiciones']

    except Exception as e:
        print(f"Error: {e}")

    # Limpieza final de filas huerfanas sin dosis
    datos_finales = [d for d in datos if es_dosis_valida(d['dosis'])]
    return datos_finales

def procesar_carpeta(carpeta):
    todos = []
    if not os.path.exists(carpeta): return
    
    archivos = [f for f in os.listdir(carpeta) if f.endswith('.pdf')]
    
    for f in archivos:
        print(f"Procesando: {f}")
        items = extraer_tabla_v7(os.path.join(carpeta, f))
        todos.extend(items)

    with open('usos_y_dosis_definitivo_v1.json', 'w', encoding='utf-8') as f:
        json.dump(todos, f, indent=4, ensure_ascii=False)
    
    print(f"✅ HECHO. {len(todos)} registros limpios de la tabla principal.")

if __name__ == "__main__":
    procesar_carpeta('mis_pdfs')