import pdfplumber
import json
import os
import re

def limpiar_espacios(texto):
    if not texto: return ""
    return ' '.join(str(texto).split()).strip()

def detectar_columnas_por_cabecera(pagina):
    """Detecta automáticamente las posiciones X de las columnas basándose en la cabecera"""
    palabras = pagina.extract_words(x_tolerance=2, y_tolerance=2)
    
    # Buscar palabras clave de la cabecera en las primeras líneas
    palabras_cabecera = [w for w in palabras if w['top'] < 150]  # Primeros 150 puntos
    
    headers = {
        'uso': None,
        'agente': None,
        'dosis': None,
        'n_aplic': None,
        'intervalo': None,
        'vol_caldo': None
    }
    
    for w in palabras_cabecera:
        texto = w['text'].lower()
        if 'uso' in texto or 'cultivo' in texto:
            headers['uso'] = w['x0']
        elif 'agente' in texto or 'plaga' in texto or 'enfermedad' in texto:
            headers['agente'] = w['x0']
        elif 'dosis' in texto:
            headers['dosis'] = w['x0']
        elif 'aplicac' in texto or 'nº' in texto:
            headers['n_aplic'] = w['x0']
        elif 'intervalo' in texto:
            headers['intervalo'] = w['x0']
        elif 'volumen' in texto or 'caldo' in texto or 'vol' in texto:
            headers['vol_caldo'] = w['x0']
    
    # Si no detectamos suficientes columnas, usar valores por defecto
    if sum(1 for v in headers.values() if v is not None) < 3:
        return None
    
    # Convertir a rangos
    posiciones = sorted([(k, v) for k, v in headers.items() if v is not None], key=lambda x: x[1])
    
    cols = {}
    for i, (nombre, x_pos) in enumerate(posiciones):
        x_min = x_pos - 5  # Margen izquierdo
        if i < len(posiciones) - 1:
            x_max = posiciones[i+1][1] - 5
        else:
            x_max = 1000  # Última columna
        cols[nombre] = (x_min, x_max)
    
    # Añadir columna de condiciones si no existe
    if 'condiciones' not in cols and 'vol_caldo' in cols:
        cols['condiciones'] = (cols['vol_caldo'][1], 1000)
    
    return cols

def limpiar_agente_avanzado(agente, dosis):
    """Limpia el agente eliminando números/unidades que pertenecen a la dosis"""
    agente = agente.strip()
    
    # Patrones que indican que hay contenido de dosis en agente
    patrones_dosis = [
        r'\s+(\d+[\.,]\d+)\s*$',  # "texto 0,5" o "texto 1.5"
        r'\s+(\d+)\s*-?\s*$',      # "texto 3 -" o "texto 5"
        r'\s+(\d+[\.,]\d+\s*-\s*\d+[\.,]?\d*)\s*',  # "texto 0,5 - 1,5"
    ]
    
    contenido_movido = []
    
    for patron in patrones_dosis:
        match = re.search(patron, agente)
        if match:
            contenido_movido.append(match.group(1))
            agente = re.sub(patron, '', agente).strip()
    
    return agente, contenido_movido

def reconstruir_dosis(dosis, contenido_agente):
    """Reconstruye la dosis con el contenido que estaba en agente"""
    if not contenido_agente:
        return dosis.strip()
    
    dosis = dosis.strip()
    
    # Si la dosis empieza con "-", añadir el número antes
    if dosis.startswith('-'):
        numero = contenido_agente[0]
        dosis = f"{numero} {dosis}"
    elif not dosis:
        dosis = ' '.join(contenido_agente)
    else:
        # Añadir al principio
        dosis = ' '.join(contenido_agente) + ' ' + dosis
    
    return normalizar_dosis(dosis)

def normalizar_dosis(dosis):
    """Normaliza formato de rangos en dosis"""
    dosis = dosis.strip()
    # "0,5 - 1,5" -> "0,5-1,5"
    dosis = re.sub(r'(\d+[\.,]\d+)\s*-\s*(\d+[\.,]?\d*)', r'\1-\2', dosis)
    # "3 - 5" -> "3-5"
    dosis = re.sub(r'(\d+)\s*-\s*(\d+)', r'\1-\2', dosis)
    # Limpiar espacios múltiples
    dosis = re.sub(r'\s+', ' ', dosis)
    return dosis.strip()

def es_basura_o_legal(texto_fila):
    """Detector de zonas prohibidas"""
    texto = texto_fila.lower()
    
    palabras_prohibidas = [
        "mitigación de riesgos", "seguridad del aplicador", "gestión de envases",
        "primeros auxilios", "almacenamiento", "eliminación del producto",
        "no contaminar", "clase y categoría", "indicaciones de peligro",
        "consejos de prudencia", "zona de protección", "banda de seguridad",
        "conforme a lo establecido", "real decreto", "orden jus",
        "euh401", "spe3", "spe8",
        "protección del consumidor", 
        "pictograma", "palabra de advertencia", "ghs0",
        "secretaria general", "dirección general", "producción agroalimentaria",
        "gobierno de españa", "ministerio de agricultura"
    ]

    return any(p in texto for p in palabras_prohibidas)

def es_dosis_valida(texto):
    """Verifica que la dosis sea válida"""
    texto = texto.lower().strip()
    if not texto: return False
    
    # Rechazar nombres de cultivos en dosis
    cultivos_falsos = ['tomate', 'pimiento', 'pepino', 'melón', 'sandía', 'ciruelo', 'cerezo', 'naranjo', 'limonero']
    if any(c in texto for c in cultivos_falsos):
        return False

    # Debe tener números O ser texto válido
    tiene_numero = bool(re.search(r'\d', texto))
    es_texto_valido = any(x in texto for x in ['consultar', 'etiqueta', 'na', 'n/a'])
    
    return (tiene_numero or es_texto_valido) and len(texto) < 100

def detectar_filas_graficas(pagina):
    """Detecta líneas horizontales"""
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

def extraer_por_lineas_y(pagina, cols):
    """Método alternativo: agrupa por coordenada Y (para PDFs sin líneas)"""
    palabras = pagina.extract_words(x_tolerance=3, y_tolerance=3)
    if not palabras:
        return []
    
    # Agrupar por Y
    lineas_y = {}
    for w in palabras:
        y = round(w['top'] / 3) * 3
        if y not in lineas_y:
            lineas_y[y] = []
        lineas_y[y].append(w)
    
    filas = []
    for y in sorted(lineas_y.keys()):
        palabras_fila = lineas_y[y]
        
        # Asignar a columnas
        texto_cols = {c: [] for c in cols}
        for w in palabras_fila:
            for col_nom, (xmin, xmax) in cols.items():
                if xmin <= w['x0'] < xmax:
                    texto_cols[col_nom].append(w['text'])
                    break
        
        fila = {k: " ".join(v).strip() for k, v in texto_cols.items()}
        filas.append(fila)
    
    return filas

def extraer_condiciones_generales(pdf_path):
    """Extrae el texto de Condiciones Generales de Uso"""
    texto_condiciones = ""
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            capturando = False
            
            for pagina in pdf.pages:
                texto = pagina.extract_text() or ""
                lineas = texto.split('\n')
                
                for linea in lineas:
                    linea_lower = linea.lower()
                    
                    # Detectar inicio
                    if 'condiciones generales de uso' in linea_lower:
                        capturando = True
                        continue
                    
                    # Detectar fin (siguiente sección)
                    if capturando and any(keyword in linea_lower for keyword in [
                        'plazos de seguridad',
                        'protección del consumidor',
                        'mitigación de riesgos',
                        'seguridad del aplicador'
                    ]):
                        break
                    
                    # Saltar líneas de página/numeración
                    if capturando:
                        # Filtrar patrones de página: "Página X de Y", números de registro, etc.
                        if re.search(r'página\s+\d+\s+de\s+\d+', linea_lower):
                            continue
                        if re.search(r'^\d{6}\s*-\s*\d{7}', linea.strip()):  # Ej: 102460 - 2025230
                            continue
                        
                        linea_limpia = linea.strip()
                        if linea_limpia:
                            texto_condiciones += linea_limpia + " "
                
                if capturando and texto_condiciones:
                    break
    
    except Exception as e:
        print(f"      ⚠️  Error extrayendo condiciones: {e}")
    
    # Limpiar posibles restos de numeración al final
    texto_condiciones = re.sub(r'Página\s+\d+\s+de\s+\d+\s*\d*\s*-?\s*\d*', '', texto_condiciones, flags=re.IGNORECASE)
    texto_condiciones = re.sub(r'\d{6}\s*-\s*\d{7}', '', texto_condiciones)
    
    return texto_condiciones.strip()

def extraer_tabla_mejorada(ruta_pdf):
    datos = []
    
    # Columnas por defecto
    cols_default = {
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
            fin_de_tabla = False
            cols = None

            for num_pag, pagina in enumerate(pdf.pages):
                if fin_de_tabla:
                    break
                
                print(f"   📄 Página {num_pag + 1}")
                
                # Intentar detectar columnas en la primera página
                if cols is None:
                    cols_detectadas = detectar_columnas_por_cabecera(pagina)
                    if cols_detectadas:
                        cols = cols_detectadas
                        print(f"      🎯 Columnas detectadas automáticamente")
                    else:
                        cols = cols_default
                        print(f"      📏 Usando columnas por defecto")
                
                # Intentar primero con líneas gráficas
                cortes = detectar_filas_graficas(pagina)
                
                if len(cortes) < 2:
                    # SIN LÍNEAS: usar método alternativo
                    print(f"      ⚠️  Sin líneas gráficas, usando agrupación por Y")
                    filas_datos = extraer_por_lineas_y(pagina, cols)
                else:
                    # CON LÍNEAS: método original
                    palabras_pag = pagina.extract_words(x_tolerance=2, y_tolerance=2)
                    filas_datos = []
                    
                    for i in range(len(cortes) - 1):
                        y_top = cortes[i]
                        y_bottom = cortes[i+1]
                        
                        palabras_fila = [w for w in palabras_pag 
                                        if y_top <= (w['top'] + (w['bottom']-w['top'])/2) < y_bottom]
                        if not palabras_fila:
                            continue

                        texto_cols = {c: [] for c in cols}
                        for w in palabras_fila:
                            for col_nom, (xmin, xmax) in cols.items():
                                if xmin <= w['x0'] < xmax:
                                    texto_cols[col_nom].append(w['text'])
                                    break
                        
                        fila = {k: " ".join(v).strip() for k, v in texto_cols.items()}
                        filas_datos.append(fila)
                
                # Procesar filas
                for fila in filas_datos:
                    texto_completo = " ".join(fila.values()).lower()

                    # STOP: Fin de tabla (cuando llega a Condiciones Generales)
                    if "condiciones generales" in texto_completo or \
                       "plazos de seguridad" in texto_completo or \
                       "protección del consumidor" in texto_completo:
                        fin_de_tabla = True
                        print(f"      🛑 Fin de tabla detectado")
                        break

                    # Cabecera - más flexible
                    if not tabla_encontrada:
                        if ("uso" in texto_completo or "cultivo" in texto_completo) and \
                           ("agente" in texto_completo or "plaga" in texto_completo or "dosis" in texto_completo):
                            tabla_encontrada = True
                            print(f"      ✅ Cabecera detectada")
                            continue
                        else:
                            continue
                    
                    # Filtros
                    if es_basura_o_legal(texto_completo):
                        continue

                    # LIMPIEZA AVANZADA: Mover contenido entre agente y dosis
                    agente_original = fila.get('agente', '')
                    dosis_original = fila.get('dosis', '')
                    
                    agente_limpio, contenido_movido = limpiar_agente_avanzado(agente_original, dosis_original)
                    dosis_corregida = reconstruir_dosis(dosis_original, contenido_movido)
                    
                    # Actualizar con valores limpios
                    fila['agente'] = agente_limpio
                    fila['dosis'] = dosis_corregida

                    if not es_dosis_valida(fila['dosis']):
                        if len(fila['dosis']) > 2 and "consultar" not in fila['dosis'].lower():
                            continue

                    # Procesar
                    if len(fila.get('uso', '')) > 2:
                        uso_actual = fila['uso']
                    
                    tiene_datos = len(fila.get('agente', '')) > 1 or len(fila.get('dosis', '')) > 0
                    
                    if tiene_datos and fila.get('agente'):
                        if "pictograma" in fila['agente'].lower():
                            continue

                        ag = fila['agente'].strip()
                        
                        if ag:
                            datos.append({
                                "uso": uso_actual,
                                "agente": ag,
                                "dosis": fila.get('dosis', ''),
                                "n_aplic": fila.get('n_aplic', ''),
                                "intervalo": fila.get('intervalo', ''),
                                "vol_caldo": fila.get('vol_caldo', ''),
                                "condiciones": fila.get('condiciones', ''),
                                "archivo": os.path.basename(ruta_pdf)
                            })
                
                if tabla_encontrada and len(datos) > 0:
                    print(f"      ✅ {len([d for d in datos if d['archivo'] == os.path.basename(ruta_pdf)])} registros")

            # Herencia de dosis
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
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()

    # Limpieza final
    datos_finales = [d for d in datos if es_dosis_valida(d['dosis'])]
    return datos_finales

def procesar_carpeta(carpeta):
    # Crear carpeta para los JSONs de salida
    carpeta_salida = 'json_output_full'
    if not os.path.exists(carpeta_salida):
        os.makedirs(carpeta_salida)
    
    if not os.path.exists(carpeta):
        print(f"❌ Carpeta '{carpeta}' no encontrada")
        return
    
    archivos = [f for f in os.listdir(carpeta) if f.endswith('.pdf')]
    
    if not archivos:
        print(f"❌ No hay PDFs en '{carpeta}'")
        return
    
    print(f"\n{'='*70}")
    print(f"🎯 EXTRACCIÓN COMPLETA - TABLA + CONDICIONES GENERALES")
    print(f"{'='*70}\n")
    
    total_registros = 0
    archivos_procesados = 0
    
    for f in archivos:
        print(f"📄 {f}")
        ruta_pdf = os.path.join(carpeta, f)
        
        # Extraer tabla principal
        items = extraer_tabla_mejorada(ruta_pdf)
        
        # Extraer condiciones generales
        condiciones_texto = extraer_condiciones_generales(ruta_pdf)
        
        if items or condiciones_texto:
            # Generar nombre del archivo JSON (mismo nombre que el PDF)
            nombre_base = os.path.splitext(f)[0]
            nombre_json = f"{nombre_base}.json"
            ruta_json = os.path.join(carpeta_salida, nombre_json)
            
            # Crear estructura combinada
            datos_completo = {
                "archivo": f,
                "tabla_principal": items,
                "condiciones_generales_uso": condiciones_texto
            }
            
            # Guardar JSON único
            with open(ruta_json, 'w', encoding='utf-8') as json_file:
                json.dump(datos_completo, json_file, indent=4, ensure_ascii=False)
            
            print(f"   💾 Guardado: {nombre_json}")
            print(f"      📊 Tabla principal: {len(items)} registros")
            if condiciones_texto:
                print(f"      📋 Condiciones generales: {len(condiciones_texto)} caracteres")
            
            total_registros += len(items)
            archivos_procesados += 1
        else:
            print(f"   ⚠️  Sin datos extraídos")
        
        print()

    print(f"{'='*70}")
    print(f"✅ COMPLETADO")
    print(f"📊 Archivos procesados: {archivos_procesados}/{len(archivos)}")
    print(f"📊 Total registros tabla principal: {total_registros}")
    print(f"📁 Carpeta de salida: {carpeta_salida}/")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    procesar_carpeta('mis_pdfs')