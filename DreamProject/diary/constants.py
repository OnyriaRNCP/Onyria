
# Dictionnaires de labels partagés
EMOTION_LABELS = {
    'heureux': 'Joie',
    'anxieux': 'Anxiété',
    'triste': 'Tristesse',
    'en_colere': 'Colère',
    'fatigue': 'Fatigue',
    'apeure': 'Peur',
    'surpris': 'Surprise',
    'serein': 'Sérénité'
}

DREAM_TYPE_LABELS = {
    'rêve': 'Rêve',
    'cauchemar': 'Cauchemar'
}

# Message d'erreur centralisé
DREAM_ERROR_MESSAGE = 'Les fils de votre rêve se sont emmêlés... Laissez-moi démêler ce songe et tentez une nouvelle analyse.'

THEME_CATEGORIES = {
    'voyage': [
        'train', 'avion', 'bateau', 'bus', 'métro', 'vélo', 'moto', 'voiture', 'taxi',
        'voyage', 'voyager', 'partir', 'arriver', 'conduire', 'voler', 'navire',
        'gare', 'aéroport', 'port', 'station', 'route', 'chemin', 'autoroute',
        'billet', 'ticket', 'valise', 'bagage', 'destination', 'vacances',
        'transport', 'circuler', 'rouler', 'embarquer', 'débarquer'
    ],
    'famille': [
        'mère', 'père', 'parent', 'frère', 'sœur', 'enfant', 'bébé', 'fils', 'fille',
        'grand-mère', 'grand-père', 'oncle', 'tante', 'cousin', 'cousine',
        'maman', 'papa', 'famille', 'familial', 'parental', 'neveu', 'nièce',
        'belle-mère', 'beau-père', 'belle-sœur', 'beau-frère', 'grands-parents'
    ],
    'travail': [
        'bureau', 'travail', 'travailler', 'job', 'emploi', 'patron', 'chef', 'collègue',
        'réunion', 'projet', 'ordinateur', 'entreprise', 'société', 'client',
        'meeting', 'présentation', 'rapport', 'dossier', 'carrière', 'salaire',
        'équipe', 'manager', 'employé', 'stagiaire', 'formation', 'commercial',
        'vente', 'marketing', 'comptabilité', 'ressources', 'humaines'
    ],
    'école': [
        'école', 'classe', 'professeur', 'prof', 'élève', 'étudiant', 'cours',
        'examen', 'test', 'diplôme', 'université', 'collège', 'lycée',
        'cahier', 'livre', 'stylo', 'tableau', 'récréation', 'cantine',
        'éducation', 'apprentissage', 'note', 'bulletin', 'devoirs', 'leçon',
        'matière', 'mathématiques', 'français', 'histoire', 'géographie',
        'sciences', 'physique', 'chimie', 'anglais', 'sport', 'bibliothèque'
    ],
    'maison': [
        'maison', 'appartement', 'chambre', 'cuisine', 'salon', 'salle', 'toilette',
        'escalier', 'porte', 'fenêtre', 'toit', 'jardin', 'garage', 'cave',
        'balcon', 'terrasse', 'lit', 'table', 'chaise', 'armoire', 'placard',
        'frigo', 'four', 'télévision', 'canapé', 'douche', 'baignoire',
        'grenier', 'sous-sol', 'couloir', 'bureau', 'dressing', 'cuisine'
    ],
    'amour': [
        'amour', 'aimer', 'copain', 'copine', 'petit-ami', 'petite-amie', 'mari',
        'femme', 'époux', 'épouse', 'baiser', 'embrasser', 'câlin', 'tendresse',
        'romance', 'romantique', 'couple', 'relation', 'mariage', 'wedding',
        'fiancé', 'fiancée', 'amoureux', 'passion', 'séduction', 'flirter',
        'rendez-vous', 'sortir', 'ensemble', 'liaison', 'aventure'
    ],
    'mort': [
        'mort', 'mourir', 'décès', 'décéder', 'enterrement', 'funérailles', 'cercueil',
        'cimetière', 'tombe', 'cadavre', 'maladie', 'hôpital', 'médecin',
        'accident', 'blessure', 'sang', 'douleur', 'agonie', 'suicide',
        'meurtre', 'tuer', 'assassiner', 'poison', 'noyade', 'chute', 'mortelle'
    ],
    'nature': [
        'mer', 'océan', 'plage', 'montagne', 'forêt', 'arbre', 'fleur', 'jardin',
        'rivière', 'lac', 'eau', 'soleil', 'lune', 'étoile', 'ciel', 'nuage',
        'pluie', 'neige', 'vent', 'orage', 'tempête', 'saison', 'printemps',
        'été', 'automne', 'hiver', 'prairie', 'champ', 'campagne', 'parc'
    ],
    'animaux': [
        'chien', 'chat', 'oiseau', 'poisson', 'cheval', 'serpent', 'araignée',
        'lion', 'tigre', 'loup', 'ours', 'souris', 'rat', 'insecte', 'mouche',
        'abeille', 'papillon', 'lapin', 'cochon', 'vache', 'mouton', 'chèvre',
        'poule', 'coq', 'canard', 'tortue', 'requin', 'dauphin', 'éléphant'
    ],
    'argent': [
        'argent', 'euro', 'dollar', 'monnaie', 'banque', 'crédit', 'dette',
        'riche', 'pauvre', 'acheter', 'vendre', 'payer', 'prix', 'coût',
        'magasin', 'boutique', 'shopping', 'cadeau', 'trésor', 'fortune',
        'compte', 'carte', 'chèque', 'espèces', 'économies', 'investissement'
    ],
    'nourriture': [
        'manger', 'nourriture', 'repas', 'petit-déjeuner', 'déjeuner', 'dîner',
        'pain', 'viande', 'poisson', 'légume', 'fruit', 'gâteau', 'chocolat',
        'restaurant', 'cuisine', 'cuisinier', 'chef', 'serveur', 'menu',
        'assiette', 'verre', 'fourchette', 'couteau', 'cuillère', 'boire',
        'eau', 'vin', 'bière', 'café', 'thé', 'jus', 'lait'
    ],
    'fête': [
        'fête', 'anniversaire', 'mariage', 'noël', 'nouvel', 'pâques',
        'halloween', 'carnaval', 'concert', 'spectacle', 'théâtre', 'cinéma',
        'musique', 'danse', 'danser', 'chanter', 'instrument', 'guitare',
        'piano', 'batterie', 'violon', 'cadeau', 'surprise', 'célébrer',
        'invité', 'soirée', 'party', 'discothèque', 'bar', 'club'
    ],
    'sport': [
        'sport', 'football', 'tennis', 'basketball', 'natation', 'course',
        'courir', 'jouer', 'équipe', 'match', 'compétition', 'champion',
        'victoire', 'défaite', 'entraînement', 'coach', 'stade', 'terrain',
        'piscine', 'gymnase', 'musculation', 'fitness', 'yoga', 'vélo',
        'escalade', 'ski', 'snowboard', 'surf'
    ],
    'guerre': [
        'guerre', 'bataille', 'combat', 'soldat', 'militaire', 'armée',
        'arme', 'pistolet', 'fusil', 'bombe', 'explosion', 'missile',
        'tank', 'avion', 'hélicoptère', 'uniforme', 'casque', 'blessé',
        'prisonnier', 'ennemi', 'allié', 'victoire', 'défaite', 'paix',
        'conflit', 'invasion', 'occupation', 'résistance'
    ],
    'religion': [
        'dieu', 'église', 'prière', 'bible', 'christ', 'jésus', 'marie',
        'ange', 'diable', 'démon', 'paradis', 'enfer', 'âme', 'esprit',
        'prêtre', 'curé', 'messe', 'communion', 'baptême', 'mariage',
        'croix', 'chapelle', 'cathédrale', 'monastère', 'couvent',
        'mosquée', 'synagogue', 'temple', 'bouddha', 'méditation'
    ],
    'technologie': [
        'ordinateur', 'téléphone', 'smartphone', 'internet', 'email',
        'facebook', 'instagram', 'twitter', 'youtube', 'google',
        'site', 'application', 'programme', 'logiciel', 'jeux', 'vidéo',
        'écran', 'clavier', 'souris', 'imprimante', 'wifi', 'bluetooth',
        'robot', 'intelligence', 'artificielle', 'technologie'
    ],
    'vêtements': [
        'vêtement', 'robe', 'pantalon', 'jupe', 'chemise', 'pull', 'manteau',
        'veste', 'chaussure', 'botte', 'sandales', 'chaussette', 'sous-vêtement',
        'chapeau', 'casquette', 'écharpe', 'gant', 'ceinture', 'sac',
        'bijou', 'collier', 'bracelet', 'bague', 'montre', 'lunettes',
        'maquillage', 'parfum', 'coiffure', 'cheveux'
    ]
}

DREAM_SPECIFIC_STOPWORDS = {
    # Mots du rêve
    'rêve',
    'rêver',
    'dormir',
    'nuit',
    'moment',
    'fois',
    'chose',
    'truc',
    'machin',
    'endroit',
    'côté',
    'genre',
    'espèce',
    'sorte',
    'façon',
    'maniÃ¨re',
    'air',
    'impression',
    'sentiment',
    'sensation',
    'souvenir',
    'souviens',
    'rappelle',
    'crois',
    'pense',
    'imagine',
    'semble',
    # Verbes trop génériques
    'faire',
    'avoir',
    'être',
    'aller',
    'dire',
    'voir',
    'savoir',
    'pouvoir',
    'vouloir',
    'devoir',
    'prendre',
    'donner',
    'mettre',
    'partir',
    'venir',
    'arriver',
    'passer',
    'rester',
    'devenir',
    'porter',
    'regarder',
    'entendre',
    'sentir',
    'trouver',
    'laisser',
    'suivre',
    'montrer',
    'demander',
    'parler',
    'tenir',
    'jouer',
    'tourner',
    'ouvrir',
    'fermer',
    'commencer',
    'finir',
    # Mots de liaison et adverbes
    'puis',
    'après',
    'avant',
    'pendant',
    'soudain',
    'tout',
    'très',
    'bien',
    'mal',
    'plus',
    'moins',
    'encore',
    'déjà',
    'toujours',
    'jamais',
    'parfois',
    'souvent',
    'beaucoup',
    'peu',
    'assez',
    'trop',
    'vraiment',
    'plutôt',
    # Mots vagues
    'personne',
    'quelqu',
    'quelque',
    'quelquun',
    'part',
    'endroit',
    'lieu',
    'temps',
    'année',
    'jour',
    'heure',
    'minute',
    'seconde',
    'vie',
    'mort',
    'histoire',
    'situation',
    'problème',
    'question',
    'réponse',
    'idée',
}

