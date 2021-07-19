import csv
import json
import os
import pickle
import logging
import numpy as np
import pandas as pd
import requests
from geopy.distance import distance, Point
from lxml import html


logger = logging.getLogger("main.enrichissement")
logger.setLevel(logging.DEBUG)


with open("config.json") as f:
    conf = json.load(f)
path_to_data = conf["path_to_data"]
siren_len = 9


def main():
    with open('df_nettoye', 'rb') as df_nettoye:
        df = pickle.load(df_nettoye)

    df = df.astype({
        'id': 'string',
        'source': 'string',
        'uid': 'string',
        'uuid': 'string',
        '_type': 'string',
        'objet': 'string',
        'codeCPV': 'string',
        'CPV_min': 'string',
        'lieuExecution.code': 'string',
        'lieuExecution.typeCode': 'string',
        'lieuExecution.nom': 'string',
        'dureeMois': 'int64',
        'montantCalcule': 'float64',
        'montantOriginal': 'float64',
        'formePrix': 'string',
        'idTitulaires': 'object',
        'denominationSociale': 'string',
        'nature': 'string',
        'acheteur.id': 'string',
        'acheteur.nom': 'string',
        'codeDepartementExecution': 'string',
        'codeRegionExecution': 'string',
        'anneeNotification': 'string',
        'moisNotification': 'string',
        'dureeMoisEstimee': 'string',
    }, copy=False)
    logger.info("Début du traitement: Enrichissement siret")
    df = enrichissement_siret(df)
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Enrichissement cpv")
    df = enrichissement_cpv(df)
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Enrichissement acheteur")
    df = enrichissement_acheteur(df)
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Reorganisation du dataframe")
    df = reorganisation(df)
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Enrichissement geographique")
    df = enrichissement_geo(df)
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Enrichissement sur le type d'entreprise")
    df = enrichissement_type_entreprise(df)
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Vérification Siren/Siret par formule de Luhn")
    df = apply_luhn(df)
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Ajout des libelle departement/Region pour les acheteurs et les etabissements")
    df = enrichissement_departement(df)  # il y a des na dans departements
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Ajout des codes/libelles des arrondissements pour les acheteurs et les etablissements")
    df = enrichissement_arrondissement(df)  # il y a des na dans departements
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Detection des accords cadre")
    # df = detection_accord_cadre(df)
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Reorganisation du dataframe final")
    df = manage_column_final(df)
    logger.info("Fin du traitement")

    logger.info("Début du traitement: Ecriture du csv final: decp_augmente")
    df.to_csv("decp_augmente.csv", quoting=csv.QUOTE_NONNUMERIC, sep=";")
    logger.info("Fin du traitement")


def subset_liste_dataframe_dec_jan(df: pd.DataFrame) -> list:
    """En entrée: Dataframe avec une colonne anneeNotification
    En sortie: une liste de dataframe <- subset de df sur Décembre 20XX et Janvier 20XX+1"""
    df2 = df.copy()  # Ainsi on opère aucune modification sur le dataframe initial
    liste_dataframe = []
    # On remplace les annees nulles par 0 pour que la boucle ne plante pas.
    # Sur année = 0, le dataframe ne contiendra que le mois de decembre.
    df2.anneeNotification = np.where(df2.anneeNotification == 'nan', '0', df2.anneeNotification)
    df2.moisNotification = np.where(df2.moisNotification.isna(), '0', df2.moisNotification)
    for annee in np.unique(df2.anneeNotification):
        sub1 = df2[(df2["anneeNotification"] == annee) & (df2["moisNotification"] == "12")]
        sub2 = df2[(df2.anneeNotification == str(int(annee) + 1)) & (df2.moisNotification == '01')]
        df_sub = pd.concat([sub1, sub2])
        liste_dataframe += [df_sub]
    return liste_dataframe

# On suppose que id permet d'identifier un marché
def detection_accord_cadre_without_date(df, compteur):
    """On va chercher à detecter les accord cadres, qu'ils soient declares ou non.
    Accord cadre : Plusieurs Etablissements sur un meme marche
    On va considerer qu un marche est definit entierement par son objet, sa date de notification, son montant et sa duree en mois.
    On fera le choix de ne pas considérer la date exacte de notification, mais le df passé en entrée correspondra aux données d'une plage précise: 1 an, 2mois ..."""
    # Creation du sub DF necessaire
    df_intermediaire = df[["objetMarche", "montantOriginal", "dureeMois", "siretEtablissement", "nature"]]
    # On regroupe selon l objet du marché. Attention, objetMarche n est pas forcément unique mais idMarche ne l'est pas non plus.
    df_group = pd.DataFrame(df_intermediaire.groupby(["objetMarche",
                                                      "montantOriginal",
                                                      "dureeMois"])
                            ["siretEtablissement"].unique())
    # Initialisation du resultat sous forme de liste
    index = df_group.index
    L_data_fram = []
    for i in range(len(df_group)):
        nombre_titulaire = len(df_group["siretEtablissement"][i])
        accord_presume = False
        if nombre_titulaire > 1:
            accord_presume = True
        compteur += 1
        L_data_fram += [[index[i][0], index[i][1], index[i][2], nombre_titulaire, str(accord_presume), compteur]]
        # L_to_join += [[objet, nb_titulaire, montantO, montantE, montantC]]
    data_to_fusion = pd.DataFrame(L_data_fram, columns=["objetMarche",
                                                        "montantOriginal",
                                                        "dureeMois",
                                                        "nombreTitulaireSurMarchePresume",
                                                        "accord-cadrePresume",
                                                        "idMarche"])

    df_to_output = pd.merge(df, data_to_fusion, how="left", left_on=["objetMarche",
                                                                     "montantOriginal",
                                                                     "dureeMois"],
                            right_on=["objetMarche",
                                      "montantOriginal",
                                      "dureeMois"])
    # Si l'une des 3 clefs à une valeur nulle alors nombreTitulaireSurMarchePresume, cadrePresume sera vide. Correction en dessous
    df_to_output["nombreTitulaireSurMarchePresume"] = np.where(df_to_output["nombreTitulaireSurMarchePresume"].isnull(),
                                                               df_to_output['nbTitulairesSurCeMarche'], df_to_output["nombreTitulaireSurMarchePresume"])
    df_to_output["accord-cadrePresume"] = np.where(df_to_output["accord-cadrePresume"].isnull(),
                                                   "False", df_to_output["accord-cadrePresume"])
    # synchronisation avec la colonne nature qui donne si c est oui ou non un accord cadre declaré
    df_to_output["nature"] = np.where(df_to_output["nature"].isnull(),
                                      "NC", df_to_output["nature"])
    df_to_output["accord-cadrePresume"] = np.where(df_to_output["nature"] != "ACCORD-CADRE",
                                                   df_to_output["accord-cadrePresume"], "True")
    return df_to_output, compteur


def detection_accord_cadre(df):
    """ Principe de fonctionnement. On detecte les accords cadre par annee sans la date
    Ensuite on detecte, toujours sans la date, sur la section Decemre-Janvier pour prendre en compte l'effet de bord.
    On compare les deux nombres de titulaires et on conserve le plus important """
    temoin_idmarche = 0
    # Création des différentes liste de dataframe: Une par année, et une autre contenant des dataframes Dec/Jan
    dataframe_annee = [df[df.anneeNotification == annee] for annee in np.unique(df.anneeNotification)]
    dataframe_mois = subset_liste_dataframe_dec_jan(df)
    # On detecte les accord-cadre sur les différents dataframe ci dessus
    for i in range(len(dataframe_annee)):
        dataframe_annee[i], temoin_idmarche = detection_accord_cadre_without_date(dataframe_annee[i], temoin_idmarche)
        # Certains Df_mois sont vides d'ou la disjonction de cas
        if len(dataframe_mois[i]) > 0:
            dataframe_mois[i], temoin_idmarche = detection_accord_cadre_without_date(dataframe_mois[i], temoin_idmarche)
        else:
            dataframe_mois[i]["nombreTitulaireSurMarchePresume"] = ""
            dataframe_mois[i]["accord-cadrePresume"] = ""
    # On passe du format liste de dataframe à un seul dataframe
    dataframe_annee = pd.concat(dataframe_annee)
    dataframe_annee = dataframe_annee.sort_index(axis=0)
    dataframe_mois = pd.concat(dataframe_mois)
    dataframe_mois = dataframe_mois.sort_index(axis=0)
    # On peut ne boucler que sur les marchés présent dans dataframe_mois.
    sub_datamois = dataframe_mois[dataframe_mois.nombreTitulaireSurMarchePresume > 1]
    # On boucle sur l'id pour identifier de façon sure les marchés. objet n'est pas unique.
    for identifiant in sub_datamois.id.unique():
        titulaire_annee = max(dataframe_annee[dataframe_annee.id == identifiant]["nombreTitulaireSurMarchePresume"])
        titulaire_mois = max(dataframe_mois[dataframe_mois.id == identifiant]["nombreTitulaireSurMarchePresume"])
        id_add = max(dataframe_mois.idMarche)
        # On localise les lignes concernées par l'identifiant et on remplace par le maximum du nombre de titulaire
        dataframe_annee.loc[dataframe_annee.id == identifiant, "nombreTitulaireSurMarchePresume"] = max(titulaire_annee, titulaire_mois)
        dataframe_annee.loc[dataframe_annee.id == identifiant, "idMarche"] = id_add + 1
    # Actualisation de la colonne booléenne
    dataframe_annee.loc[dataframe_annee.nombreTitulaireSurMarchePresume > 1, "accord-cadrePresume"] = True
    # Calcul de la colonne montantCalcule
    dataframe_annee["montantCalcule"] = dataframe_annee["montant"] / dataframe_annee["nombreTitulaireSurMarchePresume"]
    return dataframe_annee.reset_index(drop=True)


def manage_column_final(df):
    """Renommage de certaines colonnes et trie des colonnes."""
    df = df.rename(columns={
        "natureObjet": "natureObjetMarche",
        "categorieEntreprise": "categorieEtablissement"
    })
    # Réorganisation finale 'codeRegionAcheteur'
    df = df.reindex(columns=['id', 'source', 'type', 'natureObjetMarche', 'objetMarche', 'codeCPV_Original', 'codeCPV', "codeCPV_division",
                             'referenceCPV',
                             'dateNotification', 'anneeNotification', 'moisNotification', 'datePublicationDonnees', 'dureeMois', 'dureeMoisEstimee', 'dureeMoisCalculee',
                             'montantOriginal', 'montantCalcule', 'nbTitulairesSurCeMarche', 'formePrix',
                             'lieuExecutionCode', 'lieuExecutionTypeCode', 'lieuExecutionNom', "codeDepartementExecution", "codeRegionExecution", "libelleRegionExecution",
                             'nature', 'procedure',

                             'idAcheteur', 'sirenAcheteurValide', 'nomAcheteur',
                             'codeRegionAcheteur', 'libelleRegionAcheteur',
                             'departementAcheteur', 'libelleDepartementAcheteur', 'codePostalAcheteur',
                             'libelleArrondissementAcheteur', 'codeArrondissementAcheteur',
                             'libelleCommuneAcheteur', 'codeCommuneAcheteur', 'superficieCommuneAcheteur', 'populationCommuneAcheteur', 'geolocCommuneAcheteur',

                             'typeIdentifiantEtablissement', 'siretEtablissement', "siretEtablissementValide", 'sirenEtablissement', 'nicEtablissement', 'sirenEtablissementValide',
                             "categorieEtablissement", 'denominationSocialeEtablissement',
                             'codeRegionEtablissement', 'libelleRegionEtablissement',
                             'libelleDepartementEtablissement', 'departementEtablissement',
                             'libelleArrondissementEtablissement', 'codeArrondissementEtablissement',
                             'codePostalEtablissement', 'adresseEtablissement', 'communeEtablissement', 'codeCommuneEtablissement',
                             'codeTypeEtablissement',
                             'superficieCommuneEtablissement', 'populationCommuneEtablissement',
                             'distanceAcheteurEtablissement',
                             'geolocCommuneEtablissement'])
    return df


def extraction_departement_from_code_postal(code_postal):
    """Renvoie le code postal en prenant en compte les territoires outre-mer
    code_postal est un str"""
    try:
        code = code_postal[:2]
        if code == "97" or code == "98":
            code = code_postal[:3]
        return code
    except IndexError:
        return "00"


def jointure_base_departement_region():
    """Permet la jointure entre la base departement de l'insee (dossier data) et la base region de l'insee"""
    # Import de la base département
    path_dep = os.path.join(path_to_data, conf["departements-francais"])
    departement = pd.read_csv(path_dep, sep=",", usecols=['dep', 'reg', 'libelle'], dtype={"dep": str, "reg": str, "libelle": str})
    # Import de la base Région
    path_reg = os.path.join(path_to_data, conf["region-fr"])
    region = pd.read_csv(path_reg, sep=",", usecols=["reg", "libelle"], dtype={"reg": str, "libelle": str})
    region.columns = ["reg", "libelle_reg"]
    # Merge des deux bases
    df_dep_reg = pd.merge(departement, region, how="left", left_on="reg", right_on="reg", copy=False)
    df_dep_reg.columns = ["code_departement", "code_region", "Nom", "Region"]
    df_dep_reg.code_region = np.where(df_dep_reg.code_region.isin(["1", "2", "3", "4", "6"]), "0" + df_dep_reg.code_region, df_dep_reg.code_region)
    return df_dep_reg


def enrichissement_departement(df):
    """Ajout des variables région et departement dans decp. Ces deux variables concernent les acheteurs et les établissements"""
    logger.info("Début de la jointure entre les deux csv Insee: Departements et Regions")
    df_dep_reg = jointure_base_departement_region()
    logger.info("Fin de la jointure")
    # Creation de deux variables récupérant le numéro du departement
    df["departementAcheteur"] = df["codePostalAcheteur"].apply(extraction_departement_from_code_postal)
    df["departementEtablissement"] = df["codePostalEtablissement"].apply(extraction_departement_from_code_postal)
    # Fusion entre Numero et numero de departement pour recuperer le nom et ou la region (pour etablissement)
    df_dep_reg.code_departement = df_dep_reg.code_departement.astype(str)
    df = pd.merge(df, df_dep_reg, how="left", left_on="departementAcheteur", right_on="code_departement", copy=False)
    df = df.rename(columns={
                   'Nom': "libelleDepartementAcheteur",
                   'Region': "libelleRegionAcheteur",
                   'code_region': "codeRegionAcheteur"
                   })
    df = df.drop(["code_departement"], axis=1)
    df = pd.merge(df, df_dep_reg, how="left", left_on="departementEtablissement", right_on="code_departement", copy=False)
    df = df.rename(columns={
                   'Nom': "libelleDepartementEtablissement",
                   'Region': "libelleRegionEtablissement",
                   'code_region': "codeRegionEtablissement"
                   })
    df = df.drop(["code_departement"], axis=1)
    return df


def enrichissement_arrondissement(df):
    """Ajout du code Arrondissement à partir du code commune et du libelle du Arrondissement à partir de son code"""
    df = get_code_arrondissement(df)
    df = get_libelle_arrondissement(df)
    return df


def get_code_arrondissement(df):
    """Ajout de la colonne code Arrondissement à partir du code commune"""
    path_to_commune = os.path.join(path_to_data, conf["commune-fr"])
    commune = pd.read_csv(path_to_commune, sep=",", usecols=['TYPECOM', 'COM', 'ARR'], dtype={"COM": str, "ARR": str})
    commune = commune[commune.TYPECOM == "COM"]
    commune.drop(['TYPECOM'], axis=1)
    df = df.merge(commune, how="left", left_on="codeCommuneAcheteur", right_on="COM", copy=False)
    df = df.drop(["COM"], axis=1)
    df = df.rename(columns={"ARR": "codeArrondissementAcheteur"})
    df = pd.merge(df, commune, how="left", left_on="codeCommuneEtablissement", right_on="COM", copy=False)
    df = df.drop(["COM"], axis=1)
    df = df.rename(columns={"ARR": "codeArrondissementEtablissement"})
    return df


def get_libelle_arrondissement(df):
    """Ajout de la colonne libelle Arrondissement à partir du code Arrondissement"""
    path_to_arrondissement = os.path.join(path_to_data, conf["arrondissement-fr"])
    arrondissement = pd.read_csv(path_to_arrondissement, sep=",", usecols=['ARR', 'LIBELLE'], dtype={"ARR": str, "LIBELLE": str})
    df = pd.merge(df, arrondissement, how="left", left_on="codeArrondissementAcheteur", right_on="ARR", copy=False)
    df = df.drop(["ARR"], axis=1)
    df = df.rename(columns={"LIBELLE": "libelleArrondissementAcheteur"})
    df = pd.merge(df, arrondissement, how="left", left_on="codeArrondissementEtablissement", right_on="ARR", copy=False)
    df = df.drop(["ARR"], axis=1)
    df = df.rename(columns={"LIBELLE": "libelleArrondissementEtablissement"})
    return df


def enrichissement_type_entreprise(df):
    logger.info('début enrichissement_type_entreprise\n')

    df = df.astype({
        'id': 'string',
        'source': 'string',
        'type': 'string',
        'objetMarche': 'object',
        'codeCPV': 'string',
        'lieuExecutionCode': 'string',
        'lieuExecutionTypeCode': 'string',
        'lieuExecutionNom': 'string',
        'dureeMois': 'int64',
        'montantCalcule': 'float64',
        'montantOriginal': 'float64',
        'formePrix': 'string',
        'typeIdentifiantEtablissement': 'object',
        'siretEtablissement': 'string',
        'denominationSocialeEtablissement': 'string',
        'natureObjet': 'object',
        'idAcheteur': 'string',
        'nomAcheteur': 'string',
        'codePostalEtablissement': 'string',
        'anneeNotification': 'string',
        'moisNotification': 'string',
        'dureeMoisEstimee': 'string',
        'procedure': 'string',
        'nbTitulairesSurCeMarche': 'int64',
        'sirenEtablissement': 'string',
        'codeTypeEtablissement': 'string',
        'codeCommuneAcheteur': 'string',
        'libelleCommuneAcheteur': 'string',
        'codePostalAcheteur': 'string',
        'nicEtablissement': 'string',
        'communeEtablissement': 'string',
        'adresseEtablissement': 'string',
    }, copy=False)

    # Recuperation de la base
    path = os.path.join(path_to_data, conf["base_ajout_type_entreprise"])
    # La base est volumineuse. Pour "optimiser la mémoire", on va segmenter l'import
    to_add = pd.DataFrame(columns=["siren", "categorieEntreprise"])
    chunksize = 1000000
    for to_add_chunk in pd.read_csv(
        path,
        chunksize=chunksize,
        usecols=["siren", "categorieEntreprise", "nicSiegeUniteLegale"],
        dtype={"siren": 'string', "categorieEntreprise": 'string', "nicSiegeUniteLegale": 'string'}
    ):
        # On doit creer Siret
        to_add_chunk["nicSiegeUniteLegale"] = to_add_chunk["nicSiegeUniteLegale"].astype(str).str.zfill(5)

        #  À Partir d'ici le siren correspond à siretEtablissement
        #  C'est la même colonne pour optimiser la mémoire
        to_add_chunk["siren"] = to_add_chunk["siren"].astype(str).str\
            .cat(to_add_chunk["nicSiegeUniteLegale"].astype(str), sep='')

        # filtrer only existing siret
        to_add = to_add.append(to_add_chunk[to_add_chunk['siren'].isin(df['siretEtablissement'])])
        del to_add_chunk

    to_add.rename(columns={"siren": "siretEtablissement"}, inplace=True)
    # # Jointure sur le Siret entre df et to_add
    df = df.merge(
        to_add[['categorieEntreprise', 'siretEtablissement']], how='left', on='siretEtablissement', copy=False)
    df["categorieEntreprise"] = np.where(df["categorieEntreprise"].isnull(), "NC", df["categorieEntreprise"])
    del to_add
    logger.info('fin enrichissement_type_entreprise\n')
    return df


# Algorithme de Luhn

def is_luhn_valid(x):
    """Application de la formule de Luhn à un nombre
    Permet la verification du numero SIREN et Siret d'un acheteur/etablissement"""
    try:
        luhn_corr = [0, 2, 4, 6, 8, 1, 3, 5, 7, 9]
        list_number_in_x = [int(i) for i in list(str(x))]
        l2 = [luhn_corr[i] if (index + 1) % 2 == 0 else i for index, i in enumerate(list_number_in_x[::-1])]
        if sum(l2) % 10 == 0:
            return True
        elif str(x)[:9] == "356000000":  # SIREN de la Poste
            if sum(list_number_in_x) % 5 == 0:
                return True
        return False
    except:
        return False


def apply_luhn(df):
    """Application de la formule de Luhn sur les siren/siret"""
    # Application sur les siren des Acheteur
    df['siren1Acheteur'] = df["idAcheteur"].str[:9]
    df_SA = pd.DataFrame(df['siren1Acheteur'])
    df_SA = df_SA.drop_duplicates(subset=['siren1Acheteur'], keep='first')
    df_SA['sirenAcheteurValide'] = df_SA['siren1Acheteur'].apply(is_luhn_valid)
    df = pd.merge(df, df_SA, how='left', on='siren1Acheteur', copy=False)
    logger.info("Nombre de Siren Acheteur jugé invalide:{}".format(len(df) - sum(df.sirenAcheteurValide)))
    del df['siren1Acheteur']
    del df_SA
    # Application sur les siren des établissements
    df['siren2Etablissement'] = df.sirenEtablissement.str[:]
    df_SE = pd.DataFrame(df['siren2Etablissement'])
    df_SE = df_SE.drop_duplicates(subset=['siren2Etablissement'], keep='first')
    df_SE['sirenEtablissementValide'] = df_SE['siren2Etablissement'].apply(is_luhn_valid)
    df = pd.merge(df, df_SE, how='left', on='siren2Etablissement', copy=False)
    logger.info("Nombre de Siren Etablissement jugé invalide:{}".format(len(df) - sum(df.sirenEtablissementValide)))
    del df['siren2Etablissement']
    del df_SE
    # Application sur les siret des établissements
    df['siret2Etablissement'] = df.siretEtablissement.str[:]
    df_SE2 = pd.DataFrame(df['siret2Etablissement'])
    df_SE2 = df_SE2.drop_duplicates(subset=['siret2Etablissement'], keep='first')
    df_SE2['siretEtablissementValide'] = df_SE2['siret2Etablissement'].apply(is_luhn_valid)
    # Merge avec le df principal
    df = pd.merge(df, df_SE2, how='left', on='siret2Etablissement', copy=False)
    logger.info("Nombre de Siret Etablissement jugé invalide:{}".format(len(df) - sum(df.siretEtablissementValide)))
    del df["siret2Etablissement"]
    del df_SE2
    # On rectifie pour les codes non-siret
    df.siretEtablissementValide = np.where(
        (df.typeIdentifiantEtablissement != 'SIRET'),
        "Non valable",
        df.siretEtablissementValide)
    return df


def enrichissement_siret(df):
    """Enrichissement des données via les codes siret/siren"""
    dfSIRET = get_siretdf_from_original_data(df)
    archiveErrorSIRET = getArchiveErrorSIRET()

    logger.info("Enrichissement insee en cours...")
    enrichissementInsee, nanSiren = get_enrichissement_insee(dfSIRET, path_to_data)
    logger.info("Enrichissement insee fini")

    logger.info("Enrichissement infogreffe en cours...")
    enrichissementScrap = get_enrichissement_scrap(nanSiren, archiveErrorSIRET)
    del archiveErrorSIRET
    logger.info("enrichissement infogreffe fini")

    logger.info("Concaténation des dataframes d'enrichissement...")
    dfenrichissement = get_df_enrichissement(enrichissementScrap, enrichissementInsee)
    del enrichissementScrap
    del enrichissementInsee
    logger.info("Fini")

    # Ajout au df principal
    df = pd.merge(df, dfenrichissement, how='outer', left_on="idTitulaires", right_on="siret", copy=False)
    del dfenrichissement
    return df


def get_siretdf_from_original_data(df):
    # Utilisation d'un dataframe intermediaire pour traiter les Siret unique

    dfSIRET = pd.DataFrame.copy(df[['idTitulaires', 'typeIdentifiant', 'denominationSociale']])
    dfSIRET = dfSIRET.drop_duplicates(subset=['idTitulaires'], keep='first')
    dfSIRET.reset_index(inplace=True, drop=True)
    dfSIRET.idTitulaires = dfSIRET.idTitulaires.astype(str)

    dfSIRET["idTitulaires"] = np.where(~dfSIRET["idTitulaires"].str.isdigit(), '00000000000000', dfSIRET.idTitulaires)

    dfSIRET.reset_index(inplace=True, drop=True)

    dfSIRET.rename(columns={
        "idTitulaires": "siret",
        "typeIdentifiant": "siren"}, inplace=True)
    dfSIRET.siren = dfSIRET.siret.str[:siren_len]
    dfSIRET.denominationSociale = dfSIRET.denominationSociale.astype(str)

    return dfSIRET


def getArchiveErrorSIRET():
    """Récupération des siret erronés"""
    archiveErrorSIRET = pd.DataFrame(columns=['siret', 'siren', 'denominationSociale'])
    logger.info('Aucune archive d\'erreur')
    return archiveErrorSIRET


def get_enrichissement_insee(dfSIRET, path_to_data):
    """Ajout des informations Adresse/Activité des entreprises via la base siren Insee"""
    # dans StockEtablissement_utf8, il y a principalement : siren, siret, nom établissement, adresse, activité principale
    path = os.path.join(path_to_data, conf["base_sirene_insee"])
    columns = [
        'siren',
        'nic',
        'siret',
        'typeVoieEtablissement',
        'libelleVoieEtablissement',
        'codePostalEtablissement',
        'libelleCommuneEtablissement',
        'codeCommuneEtablissement',
        'activitePrincipaleEtablissement',
        'nomenclatureActivitePrincipaleEtablissement']  # Colonne à utiliser dans la base Siren
    dtypes = {
        'siret': 'string',
        'typeVoieEtablissement': 'string',
        'libelleVoieEtablissement': 'string',
        'codePostalEtablissement': 'string',
        'libelleCommuneEtablissement': 'string',
    }

    result = pd.DataFrame(columns=columns)
    chunksize = 1000000
    for gm_chunk in pd.read_csv(path, chunksize=chunksize, sep=',', encoding='utf-8', usecols=columns, dtype=dtypes):
        resultTemp = pd.merge(dfSIRET['siret'], gm_chunk, on=['siret'], copy=False)
        result = pd.concat([result, resultTemp], axis=0, copy=False)
        del resultTemp
    result = result.drop_duplicates(subset=['siret'], keep='first')

    enrichissement_insee_siret = pd.merge(dfSIRET, result, how='outer', on=['siret'], copy=False)
    enrichissement_insee_siret.rename(columns={"siren_x": "siren"}, inplace=True)
    enrichissement_insee_siret.drop(columns=["siren_y"], axis=1, inplace=True)
    nanSiret = enrichissement_insee_siret[enrichissement_insee_siret.activitePrincipaleEtablissement.isnull()]
    enrichissement_insee_siret = enrichissement_insee_siret[
        enrichissement_insee_siret.activitePrincipaleEtablissement.notnull()]
    nanSiret = nanSiret.loc[:, ["siret", "siren", "denominationSociale"]]

    # Concaténation des deux resultats
    enrichissementInsee = enrichissement_insee_siret

    temp_df = pd.merge(nanSiret, result, indicator=True, how="outer", on='siren', copy=False)
    del result
    nanSiret = temp_df[temp_df['activitePrincipaleEtablissement'].isnull()]
    nanSiret = nanSiret.iloc[:, :3]
    nanSiret.reset_index(inplace=True, drop=True)

    return [enrichissementInsee, nanSiret]


def get_enrichissement_scrap(nanSiren, archiveErrorSIRET):
    # Enrichissement des données restantes

    # ....... Solution complémentaire pour ceux non-identifié dans la BDD
    columns = [
        'index',
        'rue',
        'siret',
        'ville',
        'typeEntreprise',
        'codeType',
        'detailsType',
        'SIRETisMatched']

    filter = 10
    nanSiren = nanSiren.iloc[:filter, :]

    df_scrap = pd.DataFrame(columns=columns)
    # Récupération des résultats
    nanSiren.reset_index(inplace=True)
    resultat = pd.merge(nanSiren, df_scrap, on='index', copy=False)
    resultatScrap1 = resultat[resultat.rue != ' ']

    # Données encore manquantes
    dfDS = resultat[resultat.rue == ' ']
    dfDS = dfDS.iloc[:, 1:4]
    dfDS.columns = ['siret', 'siren', 'denominationSociale']
    dfDS.reset_index(inplace=True, drop=True)
    df_scrap2 = pd.DataFrame(
        columns=['index', 'rue', 'siret', 'ville', 'typeEntreprise', 'codeType', 'detailsType', 'SIRETisMatched'])

    # Récupération des résultats
    dfDS.reset_index(inplace=True)
    resultat = pd.merge(dfDS, df_scrap2, on='index', copy=False)
    resultatScrap2 = resultat[resultat.rue != ' ']

    ###############################################################################
    # Enregistrement des entreprises n'ayant aucune correspondance
    errorSIRET = resultat[
        (resultat.siret_y == '') | (resultat.siret_y == '') | (resultat.siret_y == ' ') | (resultat.siret_y.isnull())]
    errorSIRET = errorSIRET[['siret_x', 'siren', 'denominationSociale']]
    errorSIRET.columns = ['siret', 'siren', 'denominationSociale']
    errorSIRET.reset_index(inplace=True, drop=True)
    errorSIRET = pd.concat([errorSIRET, archiveErrorSIRET], axis=0, copy=False)
    errorSIRET = errorSIRET.drop_duplicates(subset=['siret', 'siren', 'denominationSociale'], keep='first')
    errorSIRET.to_csv('errorSIRET.csv', sep=';', index=False, header=True, encoding='utf-8')
    ###############################################################################

    # On réuni les résultats du scraping
    enrichissementScrap = pd.concat([resultatScrap1, resultatScrap2], copy=False)
    return enrichissementScrap


def get_scrap_dataframe(index, code):
    url = 'https://www.infogreffe.fr/entreprise-societe/' + code

    page = requests.get(url)
    tree = html.fromstring(page.content)

    rueSiret = tree.xpath('//div[@class="identTitreValeur"]/text()')
    infos = tree.xpath('//p/text()')
    details = tree.xpath('//a/text()')

    rue = rueSiret[1]
    siret = rueSiret[5].replace(" ", "")
    ville = infos[7]
    typeEntreprise = infos[15]
    codeType = infos[16].replace(" : ", "")
    detailsType1 = details[28]
    detailsType2 = details[29]

    if len(code) == 9:
        SIRETisMatched = siret[:9] == code
    else:
        SIRETisMatched = siret == code

    if (detailsType1 == ' '):
        detailsType = detailsType2
    else:
        detailsType = detailsType1

    if not SIRETisMatched:
        codeSiret = tree.xpath('//span[@class="data ficheEtablissementIdentifiantSiret"]/text()')
        infos = tree.xpath('//span[@class="data"]/text()')

        rue = infos[8]
        siret = codeSiret[0].replace(" ", "")
        ville = infos[9].replace(",\xa0", "")
        typeEntreprise = infos[4]
        detailsType = infos[11]
        SIRETisMatched = (siret == code)

    scrap = pd.DataFrame([index, rue, siret, ville, typeEntreprise, codeType, detailsType, SIRETisMatched]).T
    scrap.columns = ['index', 'rue', 'siret', 'ville', 'typeEntreprise', 'codeType', 'detailsType', 'SIRETisMatched']
    return scrap


def get_df_enrichissement(enrichissementScrap, enrichissementInsee):
    # Arrangement des colonnes
    # Gestion bdd insee
    enrichissementInsee.reset_index(inplace=True, drop=True)
    listCorrespondance = {
        'ALL': 'Allée',
        'AV': 'Avenue',
        'BD': 'Boulevard',
        'CAR': 'Carrefour',
        'CHE': 'Chemin',
        'CHS': 'Chaussée',
        'CITE': 'Cité',
        'COR': 'Corniche',
        'CRS': 'Cours',
        'DOM': 'Domaine',
        'DSC': 'Descente',
        'ECA': 'Ecart',
        'ESP': 'Esplanade',
        'FG': 'Faubourg',
        'GR': 'Grande Rue',
        'HAM': 'Hameau',
        'HLE': 'Halle',
        'IMP': 'Impasse',
        'LD': 'Lieu dit',
        'LOT': 'Lotissement',
        'MAR': 'Marché',
        'MTE': 'Montée',
        'PAS': 'Passage',
        'PL': 'Place',
        'PLN': 'Plaine',
        'PLT': 'Plateau',
        'PRO': 'Promenade',
        'PRV': 'Parvis',
        'QUA': 'Quartier',
        'QUAI': 'Quai',
        'RES': 'Résidence',
        'RLE': 'Ruelle',
        'ROC': 'Rocade',
        'RPT': 'Rond Point',
        'RTE': 'Route',
        'RUE': 'Rue',
        'SEN': 'Sentier',
        'SQ': 'Square',
        'TPL': 'Terre-plein',
        'TRA': 'Traverse',
        'VLA': 'Villa',
        'VLGE': 'Village'}

    enrichissementInsee['typeVoieEtablissement'].replace(listCorrespondance, inplace=True)
    enrichissementInsee['rue'] = \
        (enrichissementInsee.typeVoieEtablissement + ' ' + enrichissementInsee.libelleVoieEtablissement)

    enrichissementInsee['activitePrincipaleEtablissement'] = enrichissementInsee[
        'activitePrincipaleEtablissement'].str.replace(".", "")

    # Gestion bdd scrap
    enrichissementScrap.reset_index(inplace=True, drop=True)
    enrichissementScrap["codePostal"] = np.nan
    enrichissementScrap["commune"] = np.nan
    enrichissementScrap.codePostal = enrichissementScrap.codePostal.astype(str)
    enrichissementScrap.commune = enrichissementScrap.ville.astype(str)
    enrichissementScrap.rue = enrichissementScrap.rue.astype(str)

    enrichissementScrap["codePostal"] = enrichissementScrap.ville.str[0:7]
    enrichissementScrap["codePostal"] = enrichissementScrap["codePostal"].str.replace(" ", "")
    enrichissementScrap["commune"] = enrichissementScrap.ville.str[7:]

    enrichissementScrap.drop(columns=["index", "siret_x", "ville", "typeEntreprise",
                                      "detailsType", "SIRETisMatched", "siret_y"],
                             inplace=True, errors="ignore")
    enrichissementInsee.drop(columns=["nic", "typeVoieEtablissement", "libelleVoieEtablissement",
                                      "nomenclatureActivitePrincipaleEtablissement"],
                             inplace=True, errors="ignore")

    # Renomme les colonnes
    enrichissementScrap.rename(columns={
        'rue': 'adresseEtablissement',
        'codeType': 'codeTypeEtablissement',
        'codePostal': 'codePostalEtablissement',
        'commune': 'communeEtablissement'
    }, inplace=True, errors="ignore")
    enrichissementInsee.rename(columns={
        'libelleCommuneEtablissement': 'communeEtablissement',
        'activitePrincipaleEtablissement': 'codeTypeEtablissement',
        'rue': 'adresseEtablissement'
    }, inplace=True, errors="ignore")

    enrichissementInsee = enrichissementInsee[[
        'siret',
        'siren',
        'denominationSociale',
        'codePostalEtablissement',
        'communeEtablissement',
        'codeCommuneEtablissement',
        'codeTypeEtablissement',
        'adresseEtablissement']]

    # df final pour enrichir les données des entreprises
    dfenrichissement = pd.concat([enrichissementInsee, enrichissementScrap], copy=False)
    dfenrichissement = dfenrichissement.astype(str)
    # On s'assure qu'il n'y ai pas de doublons
    dfenrichissement = dfenrichissement.drop_duplicates(subset=['siret'], keep=False)

    return dfenrichissement


def enrichissement_cpv(df):
    """Récupération des codes CPV formatés."""
    # Importation et mise en forme des codes/ref CPV
    path = os.path.join(path_to_data, conf["cpv_2008_ver_2013"])
    refCPV = pd.read_excel(path, usecols=['CODE', 'FR'])
    refCPV.columns = ['CODE', 'refCodeCPV']
    refCPV_min = pd.DataFrame.copy(refCPV, deep=True)
    refCPV_min["CODE"] = refCPV_min.CODE.str[0:8]
    refCPV_min = refCPV_min.drop_duplicates(subset=['CODE'], keep='first')
    refCPV_min.columns = ['CODEmin', 'FR2']
    # Merge avec le df principal
    df = pd.merge(df, refCPV, how='left', left_on="codeCPV", right_on="CODE", copy=False)
    # del refCPV
    df = pd.merge(df, refCPV_min, how='left', left_on="codeCPV", right_on="CODEmin", copy=False)
    del refCPV_min
    # Garde uniquement la colonne utile / qui regroupe les nouvelles infos
    df.refCodeCPV = np.where(df.refCodeCPV.isnull(), df.FR2, df.refCodeCPV)
    df.drop(columns=["FR2", "CODE", "CODEmin"], inplace=True)
    df = pd.merge(df, refCPV, how='left', left_on="refCodeCPV", right_on="refCodeCPV", copy=False)
    del refCPV
    # Rename la variable CODE en codeCPV
    df.rename(columns={"codeCPV": "codeCPV_Original",
              "CODE": "codeCPV"}, inplace=True)
    return df


def enrichissement_acheteur(df):
    # Enrichissement des données des acheteurs #
    # Enrichissement des données via les codes siret/siren #
    # Utilisation d'un autre data frame pour traiter les Siret unique : acheteur.id
    dfAcheteurId = df['acheteur.id'].to_frame()
    dfAcheteurId.columns = ['siret']
    dfAcheteurId = dfAcheteurId.drop_duplicates(keep='first')
    dfAcheteurId.reset_index(inplace=True, drop=True)
    dfAcheteurId = dfAcheteurId.astype(str)

    # StockEtablissement_utf8
    chemin = os.path.join(path_to_data, conf["base_sirene_insee"])
    # chemin = 'dataEnrichissement/StockEtablissement_utf8.csv'
    result = pd.DataFrame(columns=['siret', 'codePostalEtablissement',
                                   'libelleCommuneEtablissement', 'codeCommuneEtablissement'])
    for gm_chunk in pd.read_csv(
            chemin, chunksize=1000000, sep=',', encoding='utf-8',
            usecols=['siret', 'codePostalEtablissement', 'libelleCommuneEtablissement', 'codeCommuneEtablissement']):
        gm_chunk['siret'] = gm_chunk['siret'].astype(str)
        resultTemp = pd.merge(dfAcheteurId, gm_chunk, on="siret", copy=False)
        result = pd.concat([result, resultTemp], axis=0, copy=False)
    result = result.drop_duplicates(subset=['siret'], keep='first')
    enrichissementAcheteur = result
    enrichissementAcheteur.columns = ['acheteur.id', 'codePostalAcheteur', 'libelleCommuneAcheteur',
                                      'codeCommuneAcheteur']
    enrichissementAcheteur = enrichissementAcheteur.drop_duplicates(subset=['acheteur.id'], keep='first')

    df = pd.merge(df, enrichissementAcheteur, how='left', on='acheteur.id', copy=False)
    del enrichissementAcheteur
    return df


def reorganisation(df):
    """Mise en qualité du dataframe"""
    # Ajustement de certaines colonnes
    df.codePostalEtablissement = df.codePostalEtablissement.astype(str).str[:5]
    df.codePostalAcheteur = df.codePostalAcheteur.astype(str).str[:5]
    df.codeCommuneEtablissement = df.codeCommuneEtablissement.astype(str).str[:5]
    df.codeCommuneAcheteur = df.codeCommuneAcheteur.astype(str).str[:5]

    df.anneeNotification = df.anneeNotification.astype(str)
    df.codeDepartementExecution = df.codeDepartementExecution.astype(str)

    # codePostal est enlevé pour le moment car est un code départemental
    df.drop(columns=["uid", "uuid", "denominationSociale_x", 'siret'], inplace=True, errors="ignore")

    # Réorganisation des colonnes et de leur nom
    column_mapping = {
        'id': "id",
        '_type': "type",
        'objet': "objetMarche",
        'lieuExecution.code': "lieuExecutionCode",
        'lieuExecution.typeCode': "lieuExecutionTypeCode",
        'lieuExecution.nom': "lieuExecutionNom",
        'acheteur.id': "idAcheteur",
        'acheteur.nom': "nomAcheteur",
        'typeIdentifiant': "typeIdentifiantEtablissement",
        'idTitulaires': "siretEtablissement",
        'denominationSociale_y': "denominationSocialeEtablissement",
        'nic': "nicEtablissement",
        'CPV_min': "codeCPV_division",
        'siren': "sirenEtablissement",
        'refCodeCPV': "referenceCPV"
    }
    df.rename(columns=column_mapping, inplace=True)

    # Rectification codePostalAcheteur et codeCommuneAcheteur
    df["codePostalAcheteur"] = df["codePostalAcheteur"].apply(fix_codegeo)
    df["codeCommuneAcheteur"] = df["codeCommuneAcheteur"].apply(fix_codegeo)
    df["codePostalEtablissement"] = df["codePostalEtablissement"].apply(fix_codegeo)
    df["codeCommuneEtablissement"] = df["codeCommuneEtablissement"].apply(fix_codegeo)
    # Petites corrections sur lieuExecutionTypeCode et nature
    list_to_correct = ["lieuExecutionTypeCode", "nature"]
    for column in list_to_correct:
        df[column] = df[column].str.upper()
        df[column] = df[column].str.replace("É", "E")
    return df


def fix_codegeo(code):
    """Correction de l'erreur ou le code 01244 soit considérer comme l'entier 1244
    code doit etre un code commune/postal"""
    if len(code) == 4:
        code = "0" + code
    if "." in code[:5]:
        return "0" + code[:4]
    return code[:5]


def enrichissement_geo(df):
    """Ajout des géolocalisations des entreprises"""
    # Enrichissement latitude & longitude avec adresse la ville
    df.codeCommuneAcheteur = df.codeCommuneAcheteur.astype(object)
    df.codeCommuneEtablissement = df.codeCommuneEtablissement.astype(object)
    df_villes = get_df_villes()
    # Ajout des données Acheteur
    df = pd.merge(df, df_villes, how='left', left_on="codeCommuneAcheteur", right_on="codeCommune", copy=False)
    df.rename(columns={"superficie": "superficieCommuneAcheteur",
                       "population": "populationCommuneAcheteur",
                       "latitude": "latitudeCommuneAcheteur",
                       "longitude": "longitudeCommuneAcheteur"},
              inplace=True)
    df.drop(columns="codeCommune", inplace=True)
    # Ajout des données Etablissements
    df = pd.merge(df, df_villes, how='left', left_on="codeCommuneEtablissement", right_on='codeCommune', copy=False)
    df.rename(columns={"superficie": "superficieCommuneEtablissement",
                       "population": "populationCommuneEtablissement",
                       "latitude": "latitudeCommuneEtablissement",
                       "longitude": "longitudeCommuneEtablissement"},
              inplace=True)
    df.drop(columns="codeCommune", inplace=True)

    # Calcul de la distance entre l'acheteur et l'etablissement
    df['distanceAcheteurEtablissement'] = df.apply(get_distance, axis=1)

    # Remise en forme des colonnes géo-spatiales
    cols = ["longitudeCommuneAcheteur",
            "latitudeCommuneAcheteur",
            "longitudeCommuneEtablissement",
            "latitudeCommuneEtablissement"]
    df[cols] = df[cols].astype(str)
    df['geolocCommuneAcheteur'] = df.latitudeCommuneAcheteur + ',' + df.longitudeCommuneAcheteur
    df['geolocCommuneAcheteur'] = np.where(
        df['geolocCommuneAcheteur'] == 'nan,nan', np.NaN, df['geolocCommuneAcheteur'])

    df['geolocCommuneEtablissement'] = df.latitudeCommuneEtablissement + ',' + df.longitudeCommuneEtablissement
    df['geolocCommuneEtablissement'] = np.where(
        df['geolocCommuneEtablissement'] == 'nan,nan', np.NaN, df['geolocCommuneEtablissement'])
    df.reset_index(inplace=True, drop=True)

    return df


def get_df_villes():
    """Récupération des informations sur les communes (superficie/population"""
    path = os.path.join(path_to_data, conf["base_geoflar"])
    df_villes = pd.read_csv(path, sep=';', header=0, error_bad_lines=False,
                            usecols=['INSEE_COM', 'Geo Point', 'SUPERFICIE', 'POPULATION'])

    # Suppression des codes communes sans point geo
    df_villes = df_villes[(df_villes['INSEE_COM'].notnull()) & (df_villes['Geo Point'].notnull())]
    df_villes.reset_index(inplace=True, drop=True)

    # Séparation de la latitude et la longitude depuis les info géo
    df_villes['Geo Point'] = df_villes['Geo Point'].astype(str)
    df_sep = pd.DataFrame(df_villes['Geo Point'].str.split(',', 1, expand=True))
    df_sep.columns = ['latitude', 'longitude']

    # Fusion des lat/long dans le df
    df_villes = df_villes.join(df_sep)

    # Suppression des colonnes inutiles
    df_villes.drop(columns=["Geo Point"], inplace=True, errors="ignore")

    # Renommer les variables
    df_villes.rename(columns={"INSEE_COM": 'codeCommune',
                              "POPULATION": 'population',
                              "SUPERFICIE": 'superficie'},
                     inplace=True)

    # Conversion des colonnes
    df_villes.population = df_villes.population.astype(float)
    df_villes.codeCommune = df_villes.codeCommune.astype(object)
    df_villes.latitude = df_villes.latitude.astype(float)
    df_villes.longitude = df_villes.longitude.astype(float)

    return df_villes


def get_distance(row):
    """Calcul des distances entre l'acheteur et l'établissement qui répond à l'offre"""
    try:
        x = Point(row.longitudeCommuneAcheteur, row.latitudeCommuneAcheteur)
        y = Point(row.longitudeCommuneEtablissement, row.latitudeCommuneEtablissement)

        return distance(x, y).km
    except ValueError:
        return None


if __name__ == "__main__":
    main()
