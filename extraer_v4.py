import pdfplumber
import json
import os
import re

def limpiar_texto(texto):
    """Limpia y normaliza el texto"""
    if not texto: 
        return ""
    return str(texto).replace('\n', ' ').replace('\r', ' ').strip()

def es_registro_valido(fila):
    """Determina si un registro es válido"""
    tiene_uso = bool(fila['uso']) and len(fila['uso']) > 1
    tiene_agente = bool(fila['agente']) and len(fila['agente']) > 1
    tiene_dosis = bool(fila['dosis']) and len(fila['dosis']) > 1
    
    # Validar que la dosis tenga formato válido
    if tiene_dosis:
        dosis_lower = fila['dosis'].lower()
        if 'uso' in dosis_lower or 'cultivo' in dosis_lower:
            return False
        if not any(c.isdigit() for c in fila['dosis']) and 'kg/ha' not in dosis_lower and 'l/ha' not in dosis_lower:
            return False
    
    return (tiene_uso or tiene_agente) and tiene_dosis

def es_linea_continuacion(fila):
    """
    Detecta si una línea es continuación de la anterior.
    Una línea de continuación típicamente:
    - NO tiene agente ni cultivo
    - Solo tiene texto en condiciones
    - O tiene datos complementarios (n_aplic, intervalo, vol_caldo)
    """
    tiene_uso = bool(fila['uso']) and len(fila['uso']) > 2
    tiene_agente = bool(fila['agente']) and len(fila['agente']) > 2
    tiene_dosis = bool(fila['dosis']) and len(fila['dosis']) > 2
    tiene_condiciones = bool(fila['condiciones']) and len(fila['condiciones']) > 2
    
    # Si solo tiene condiciones o datos complementarios, es continuación
    if tiene_condiciones and not tiene_agente and not tiene_dosis and not tiene_uso:
        return True
    
    # Si tiene datos complementarios sin agente ni cultivo, probablemente es continuación
    tiene_complementos = bool(fila['n_aplic']) or bool(fila['intervalo']) or bool(fila['vol_caldo'])
    if tiene_complementos and not tiene_agente and not tiene_uso:
        return True
    
    return False

def fusionar_registros_relacionados(registros):
    """
    Fusiona registros que son continuación de otros.
    Ejemplo: Una línea con agente + dosis, seguida de líneas solo con condiciones.
    """
    if not registros:
        return registros
    
    fusionados = []
    registro_actual = None
    
    for registro in registros:
        # Si tenemos un registro actual y este parece continuación
        if registro_actual and es_linea_continuacion(registro):
            # Fusionar con el actual
            for campo in ['condiciones', 'n_aplic', 'intervalo', 'vol_caldo']:
                if registro[campo] and not registro_actual[campo]:
                    registro_actual[campo] = registro[campo]
                elif registro[campo] and registro_actual[campo]:
                    # Concatenar si ambos tienen contenido
                    if campo == 'condiciones':
                        registro_actual[campo] = f"{registro_actual[campo]} {registro[campo]}"
        else:
            # Es un nuevo registro independiente
            if registro_actual:
                fusionados.append(registro_actual)
            registro_actual = registro.copy()
    
    # Agregar el último
    if registro_actual:
        fusionados.append(registro_actual)
    
    return fusionados

def propagar_celdas_vacias(registros):
    """
    Propaga valores de columnas vacías de forma BIDIRECCIONAL.
    """
    if not registros:
        return registros
    
    # Columnas que se deben propagar
    columnas_propagar = ['uso', 'n_aplic', 'intervalo', 'vol_caldo']
    
    # PASO 1: Propagar hacia ABAJO (normal)
    for i in range(1, len(registros)):
        for columna in columnas_propagar:
            if not registros[i].get(columna) or registros[i][columna].strip() == '':
                registros[i][columna] = registros[i-1].get(columna, '')
    
    # PASO 2: Propagar hacia ARRIBA (reversa)
    for columna in columnas_propagar:
        ultimo_valor = ''
        for i in range(len(registros) - 1, -1, -1):
            if registros[i].get(columna) and registros[i][columna].strip():
                ultimo_valor = registros[i][columna]
            elif ultimo_valor and (not registros[i].get(columna) or registros[i][columna].strip() == ''):
                registros[i][columna] = ultimo_valor
    
    return registros

def extraer_tabla_inteligente(ruta_pdf):
    """Extrae tabla con detección inteligente de registros"""
    datos = []
    
    rangos_columnas = {
        'uso': (0, 110),
        'agente': (110, 265),
        'dosis': (265, 350),
        'n_aplic': (350, 405),
        'intervalo': (405, 485),
        'vol_caldo': (485, 600),
        'condiciones': (600, 9999)
    }
    
    try:
        with pdfplumber.open(ruta_pdf) as pdf:
            for num_pag, pagina in enumerate(pdf.pages):
                print(f"   📄 Página {num_pag + 1}")
                
                palabras = pagina.extract_words(
                    x_tolerance=3,
                    y_tolerance=3,
                    keep_blank_chars=False
                )
                
                if not palabras:
                    continue
                
                # Agrupar por línea con mayor tolerancia
                lineas = {}
                for palabra in palabras:
                    # Mayor tolerancia en Y para capturar líneas cercanas
                    y = round(palabra['top'] / 5) * 5
                    if y not in lineas:
                        lineas[y] = []
                    lineas[y].append({
                        'x': palabra['x0'],
                        'texto': palabra['text']
                    })
                
                # Buscar cabecera
                y_cabecera = None
                for y in sorted(lineas.keys()):
                    texto_linea = ' '.join([p['texto'] for p in lineas[y]]).lower()
                    if ('uso' in texto_linea or 'cultivo' in texto_linea) and \
                       ('agente' in texto_linea or 'plaga' in texto_linea):
                        y_cabecera = y
                        print(f"      ✅ Cabecera detectada")
                        break
                
                if not y_cabecera:
                    print("      ⚠️  Sin cabecera")
                    continue
                
                # Procesar cada línea como un registro INDEPENDIENTE
                registros_pagina = []
                
                for y in sorted(lineas.keys()):
                    if y <= y_cabecera:
                        continue
                    
                    palabras_linea = sorted(lineas[y], key=lambda p: p['x'])
                    texto_completo = ' '.join([p['texto'] for p in palabras_linea]).lower()
                    
                    # Filtros de líneas no deseadas
                    if len(texto_completo) < 2:
                        continue
                    
                    filtros = [
                        'usos y dosis',
                        'ámbito de aplicación',
                        'tipo de usuario',
                        'sistema de cultivo',
                        'método de aplicación',
                        'plazos de seguridad',
                        'protección del consumidor',
                        'condiciones generales',
                        'seguridad del aplicador',
                        'seguridad del trabajador',
                        'mitigación de riesgos',
                        'mezcla, carga',
                        'aplicación:',
                        'página',
                        'ministerio',
                        'gobierno',
                        'equipo adecuado',
                        'nebulización',
                        'sistemas de cultivo',
                        'fabricante',
                        'titular',
                        'conclusiones del',
                        'evaluación de',
                        'han sido solicitados',
                        'han sido evaluados',
                        'real decreto'
                    ]
                    
                    if any(f in texto_completo for f in filtros):
                        continue
                    
                    # Asignar palabras a columnas
                    fila_datos = {col: [] for col in rangos_columnas.keys()}
                    
                    for palabra in palabras_linea:
                        x = palabra['x']
                        texto = palabra['texto']
                        
                        asignada = False
                        for col_nombre, (x_min, x_max) in rangos_columnas.items():
                            if x_min <= x < x_max:
                                fila_datos[col_nombre].append(texto)
                                asignada = True
                                break
                        
                        if not asignada:
                            fila_datos['condiciones'].append(texto)
                    
                    # Crear registro de esta línea
                    registro = {
                        col: ' '.join(palabras).strip()
                        for col, palabras in fila_datos.items()
                    }
                    
                    # Guardar TODAS las líneas (incluso vacías en algunos campos)
                    # Ya no filtramos aquí, lo haremos después
                    registros_pagina.append(registro)
                
                # PASO 1: Fusionar líneas de continuación
                registros_pagina = fusionar_registros_relacionados(registros_pagina)
                
                # PASO 2: Propagar celdas vacías BIDIRECCIONAL
                registros_pagina = propagar_celdas_vacias(registros_pagina)
                
                # PASO 3: Extraer agentes de condiciones si es necesario
                for registro in registros_pagina:
                    # Caso 1: "Agente: ..." en condiciones
                    if not registro['agente'] and 'agente:' in registro['condiciones'].lower():
                        match = re.search(r'agente:\s*([^.]+)', registro['condiciones'], re.IGNORECASE)
                        if match:
                            registro['agente'] = match.group(1).strip()
                            registro['condiciones'] = registro['condiciones'].replace(match.group(0), '').strip()
                    
                    # Caso 2: Plagas al inicio de condiciones
                    if not registro['agente'] and registro['condiciones']:
                        plagas_patron = r'^(Nematodos de los nódulos|Meloidogyne\s+\w+|Áfidos|Trips|Moscas blancas|Caracoles|Lepidópteros|Pulgones|Aphididae|Aleyrodidae)'
                        match = re.search(plagas_patron, registro['condiciones'], re.IGNORECASE)
                        if match:
                            registro['agente'] = match.group(1).strip()
                            registro['condiciones'] = registro['condiciones'][match.end():].strip().lstrip(',').strip()
                
                # PASO 4: Validar y agregar
                registros_validos = []
                for registro in registros_pagina:
                    if es_registro_valido(registro):
                        registros_validos.append(registro)
                
                datos.extend(registros_validos)
                print(f"      📦 {len(registros_validos)} registros válidos")
    
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    return datos

def limpiar_agente(texto):
    """Limpia el campo agente"""
    if not texto:
        return texto
    
    # Eliminar números sueltos al final (son parte de dosis)
    texto = re.sub(r'\s+\d+[,\.]\d*\s*$', '', texto)
    
    # Limpiar espacios múltiples
    texto = ' '.join(texto.split())
    
    # Eliminar comas finales sueltas
    texto = texto.rstrip(',').strip()
    
    return texto

def extraer_numero_dosis(texto):
    """Extrae el número de la dosis si está en el texto"""
    match = re.search(r'(\d+[,\.]\d+)', texto)
    if match:
        return match.group(1)
    return None

def postprocesar_registro(item):
    """Limpia y ajusta un registro"""
    
    # 1. Limpiar agente
    item['agente'] = limpiar_agente(item['agente'])
    
    # 2. Buscar números de dosis en el agente y moverlos
    if item['agente']:
        numero_en_agente = extraer_numero_dosis(item['agente'])
        if numero_en_agente:
            # Si la dosis no tiene números, usar el del agente
            if not any(c.isdigit() for c in item['dosis']):
                item['dosis'] = f"{numero_en_agente} {item['dosis']}"
            # Eliminar el número del agente
            item['agente'] = re.sub(r'\s*\d+[,\.]\d+\s*', ' ', item['agente']).strip()
    
    # 3. Separar vol_caldo de condiciones si está mezclado
    if item['vol_caldo']:
        match = re.match(r'^([\d\s\-]+\s*l/ha)\s+(.+)$', item['vol_caldo'], re.IGNORECASE)
        if match:
            item['vol_caldo'] = match.group(1).strip()
            texto_extra = match.group(2).strip()
            item['condiciones'] = f"{texto_extra} {item['condiciones']}".strip()
    
    # 4. Limpiar "NA" en vol_caldo
    if item['vol_caldo'].upper() == 'NA':
        item['vol_caldo'] = 'N/A'
    
    # 5. Limpiar espacios duplicados
    for key in item:
        if isinstance(item[key], str):
            item[key] = ' '.join(item[key].split())
    
    # 6. Limpiar texto repetido en condiciones
    if item['condiciones']:
        # Eliminar frases duplicadas
        frases = item['condiciones'].split('.')
        frases_unicas = []
        for frase in frases:
            frase = frase.strip()
            if frase and frase not in frases_unicas:
                frases_unicas.append(frase)
        item['condiciones'] = '. '.join(frases_unicas)
        if item['condiciones'] and not item['condiciones'].endswith('.'):
            if item['condiciones'] and not item['condiciones'][-1] in '.!?':
                item['condiciones'] += '.'
    
    # 7. Limpiar condiciones vacías o con solo puntos
    if item['condiciones'] and item['condiciones'].strip() in ['.', '..', '...']:
        item['condiciones'] = ''
    
    return item

def limpiar_registros(datos):
    """Limpieza final de registros"""
    limpios = []
    
    for item in datos:
        # Postprocesar
        item = postprocesar_registro(item)
        
        # Validaciones básicas
        texto_total = ' '.join(item.values()).lower()
        
        # Descartar si muy corto
        if len(texto_total) < 10:
            continue
        
        # Descartar texto administrativo
        basura = [
            'secretaria',
            'ministerio',
            'gobierno',
            'normativa',
            'reglamento',
            'conclusiones del',
            'han sido evaluados',
            'han sido solicitados',
            'criterios de evaluación',
            'real decreto'
        ]
        
        if any(b in texto_total for b in basura):
            continue
        
        # Descartar si USO es muy largo
        if len(item['uso']) > 200:
            continue
        
        # Descartar si tiene "USO" en la dosis
        if 'uso' in item['dosis'].lower():
            continue
        
        # Descartar si la dosis no parece válida
        if item['dosis']:
            tiene_numeros = any(c.isdigit() for c in item['dosis'])
            tiene_unidades = any(u in item['dosis'].lower() for u in ['l/ha', 'kg/ha', 'l/tm', 'g/ha', 'ml/ha', 'g/m'])
            
            if not tiene_numeros and not tiene_unidades:
                continue
        
        limpios.append(item)
    
    return limpios

def eliminar_duplicados(datos):
    """Elimina registros duplicados"""
    vistos = set()
    unicos = []
    
    for item in datos:
        # Crear clave única más precisa
        clave = f"{item['uso']}|{item['agente']}|{item['dosis']}|{item['n_aplic']}|{item['intervalo']}"
        
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(item)
    
    return unicos

def procesar_carpeta(carpeta):
    """Procesa todos los PDFs y genera un JSON por cada uno"""
    
    if not os.path.exists(carpeta):
        print(f"❌ Carpeta '{carpeta}' no encontrada")
        return
    
    # Crear carpeta de salida si no existe
    carpeta_salida = 'json_output'
    if not os.path.exists(carpeta_salida):
        os.makedirs(carpeta_salida)
    
    archivos = [f for f in os.listdir(carpeta) if f.endswith('.pdf')]
    
    if not archivos:
        print(f"❌ No hay PDFs en '{carpeta}'")
        return
    
    print(f"\n{'='*70}")
    print(f"🎯 EXTRACCIÓN MEJORADA - CAPTURA MULTILÍNEA COMPLETA")
    print(f"{'='*70}\n")
    
    total_archivos_procesados = 0
    total_registros = 0
    
    for archivo in archivos:
        ruta = os.path.join(carpeta, archivo)
        print(f"📄 {archivo}")
        
        datos = extraer_tabla_inteligente(ruta)
        
        if datos:
            # Limpiar
            datos = limpiar_registros(datos)
            
            # Eliminar duplicados de este PDF
            datos = eliminar_duplicados(datos)
            
            # Agregar nombre de archivo a cada registro
            for item in datos:
                item['archivo'] = archivo
            
            # Generar nombre del JSON
            nombre_base = os.path.splitext(archivo)[0]
            archivo_json = os.path.join(carpeta_salida, f"{nombre_base}.json")
            
            # Guardar JSON individual
            with open(archivo_json, 'w', encoding='utf-8') as f:
                json.dump(datos, f, indent=4, ensure_ascii=False)
            
            print(f"   ✅ {len(datos)} registros → {archivo_json}")
            print()
            
            total_archivos_procesados += 1
            total_registros += len(datos)
        else:
            print(f"   ⚠️  Sin datos\n")
    
    print(f"{'='*70}")
    print(f"✅ COMPLETADO")
    print(f"📊 Archivos procesados: {total_archivos_procesados}")
    print(f"📊 Total registros: {total_registros}")
    print(f"📁 Carpeta de salida: {carpeta_salida}/")
    print(f"{'='*70}\n")
    
    # Listar archivos generados
    archivos_json = [f for f in os.listdir(carpeta_salida) if f.endswith('.json')]
    if archivos_json:
        print("📋 ARCHIVOS JSON GENERADOS:\n")
        for i, arch in enumerate(sorted(archivos_json), 1):
            ruta_json = os.path.join(carpeta_salida, arch)
            with open(ruta_json, 'r', encoding='utf-8') as f:
                datos = json.load(f)
            print(f"  {i}. {arch} ({len(datos)} registros)")
        print()

if __name__ == "__main__":
    procesar_carpeta('mis_pdfs')