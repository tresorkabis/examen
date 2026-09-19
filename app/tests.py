"""Suite de tests de l'application « app ».

Couvre : les calculs de moyenne (modèle Inscription), la fiche de cote,
l'inscription/désinscription des étudiants, la pagination hors bornes et
l'authentification (accès en écriture protégé).
"""
from datetime import date, datetime
from io import BytesIO

from django.core.cache import cache
from django.core.management import call_command
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Promotion, Enseignant, Etudiant, Cours, Session,
                     Examen, Inscription, Grille, GrilleUE, GrilleEtudiant,
                     GrilleNote)
from .forms import ExamenForm


class BaseDataMixin:
    """Crée les fixtures communes à tous les tests."""

    @classmethod
    def setUpTestData(cls):
        cls.promotion = Promotion.objects.create(nom='L3 INFO A')
        cls.enseignant = Enseignant.objects.create(
            noms='KABISAYI TRESOR',
            email='tkabisayi@example.com')
        cls.cours = Cours.objects.create(
            nom='Langage de programmation mobile',
            coefficient=1, enseignant=cls.enseignant,
            promotion=cls.promotion)
        cls.session = Session.objects.create(
            nom='SESSION 1, SEMESTRE 1 2025 - 2026',
            semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        cls.examen = Examen.objects.create(
            cours=cls.cours, session=cls.session,
            date_examen=timezone.make_aware(datetime(2026, 1, 15, 8, 0)),
            salle='Local 1')

    @classmethod
    def _creer_etudiants(cls, nb=3):
        for i in range(nb):
            Etudiant.objects.create(
                noms=f'NOM{i} Prenom{i}',
                email=f'etu{i}@example.com',
                numero_etudiant=f'L3INFOA-{i:03d}',
                promotion=cls.promotion)

    def _connexion(self):
        user = User.objects.create_user('admin', 'admin@example.com', 'pass12345')
        self.client.force_login(user)
        return user

    def _inscriptions(self, nb=2):
        """Inscrit les nb premiers étudiants de la promotion à l'examen."""
        etudiants = list(Etudiant.objects.filter(promotion=self.promotion)
                         .order_by('numero_etudiant')[:nb])
        for e in etudiants:
            Inscription.objects.create(examen=self.examen, etudiant=e)
        return etudiants


class ModelMoyenneTests(TestCase):
    """Calcul de la moyenne /20 dans le modèle Inscription."""

    @classmethod
    def setUpTestData(cls):
        cls.promotion = Promotion.objects.create(nom='L3 INFO A')
        cls.cours = Cours.objects.create(
            nom='Mathématiques', coefficient=1, promotion=cls.promotion)
        cls.session = Session.objects.create(
            nom='SESSION 1', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        cls.examen = Examen.objects.create(
            cours=cls.cours, session=cls.session,
            date_examen=timezone.make_aware(datetime(2026, 1, 15, 8, 0)))
        cls.etudiant = Etudiant.objects.create(
            noms='DUPONT Jean',
            email='jean.dupont@example.com',
            numero_etudiant='L3INFOA-901',
            promotion=cls.promotion)

    def test_moyenne_none_quand_tout_vide(self):
        ins = Inscription.objects.create(examen=self.examen,
                                          etudiant=self.etudiant)
        self.assertIsNone(ins.moyenne)
        self.assertIsNone(ins.note)

    def test_moyenne_somme_des_composantes(self):
        ins = Inscription.objects.create(
            examen=self.examen, etudiant=self.etudiant,
            note_interro=4, note_tp=3.5, note_examen=8)
        self.assertEqual(ins.moyenne, 15.5)
        self.assertEqual(ins.note, 15.5)

    def test_save_recalcule_la_note(self):
        ins = Inscription.objects.create(
            examen=self.examen, etudiant=self.etudiant,
            note_interro=5, note_tp=5, note_examen=10)
        ins.note_examen = 0
        ins.save()
        self.assertEqual(ins.note, 10)  # 5 + 5 + 0


class ModelesOptimisationTests(TestCase):
    """Invariants de modèle ajoutés pour sécuriser et accélérer les imports."""

    @classmethod
    def setUpTestData(cls):
        cls.promotion = Promotion.objects.create(nom='L1 INFO A')

    def test_cours_homonyme_meme_promotion_interdit(self):
        """Contrainte (promotion, nom) : un cours ne se duplique pas dans une promo."""
        from django.db import IntegrityError, transaction
        Cours.objects.create(nom='Anglais', promotion=self.promotion)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cours.objects.create(nom='Anglais', promotion=self.promotion)

    def test_requete_inscription_sans_jointure_implicite(self):
        """Sans `ordering` sur une FK, Inscription ne joint plus Etudiant."""
        sql = str(Inscription.objects.filter(examen_id=1).query)
        self.assertNotIn('JOIN', sql)
        self.assertNotIn('ORDER BY', sql)

    def test_numero_etudiant_non_genere_a_l_instanciation(self):
        """Le matricule n'est plus un `default=` : aucune requête à l'instanciation."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            etudiant = Etudiant(noms='SANS NUMERO', promotion=self.promotion)

        self.assertEqual(len(ctx), 0)
        self.assertEqual(etudiant.numero_etudiant, '')
        etudiant.save()
        self.assertRegex(etudiant.numero_etudiant, r'^ETU-\d{8}$')


class FicheCoteTests(BaseDataMixin, TestCase):
    """Endpoint /examens/<pk>/fiche/."""

    def test_fiche_anonyme_redirige_vers_login(self):
        response = self.client.get(reverse('examen_fiche',
                                           kwargs={'pk': self.examen.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_fiche_affiche_les_infos_sans_salle(self):
        self._connexion()
        self._creer_etudiants(3)
        self._inscriptions(2)
        response = self.client.get(reverse('examen_fiche',
                                           kwargs={'pk': self.examen.pk}))
        self.assertEqual(response.status_code, 200)
        contenu = response.content.decode()
        # Titulaire, Promotion, Intitulé
        self.assertIn('Titulaire :', contenu)
        self.assertIn('KABISAYI TRESOR', contenu)
        self.assertIn('Promotion :', contenu)
        self.assertIn('L3 INFO A', contenu)
        self.assertIn('Intitul\u00e9 du Cours :', contenu)
        self.assertIn('Langage de programmation mobile', contenu)
        # Salle absente de la fiche
        self.assertNotIn('Salle :', contenu)
        # « Nombre total d'étudiants » affiché après le tableau
        self.assertIn("Nombre total d'étudiants :</strong> 2</p>", contenu)

    def test_fiche_n_affiche_que_les_inscrits(self):
        self._connexion()
        self._creer_etudiants(3)
        self._inscriptions(1)
        response = self.client.get(reverse('examen_fiche',
                                           kwargs={'pk': self.examen.pk}))
        contenu = response.content.decode()
        self.assertIn("Nombre total d'étudiants :</strong> 1</p>", contenu)
        # Une seule ligne dans le tableau (les autres étudiants n'apparaissent
        # que dans le sélecteur d'ajout, pas dans la fiche).
        self.assertEqual(contenu.count('<td class="num">'), 1)


class InscriptionViewTests(BaseDataMixin, TestCase):
    """Flux d'inscription / désinscription des étudiants à un examen."""

    def test_inscrire_un_etudiant(self):
        self._connexion()
        self._creer_etudiants(2)
        etudiant = Etudiant.objects.get(numero_etudiant='L3INFOA-000')
        response = self.client.post(
            reverse('examen_inscrire', kwargs={'pk': self.examen.pk}),
            {'etudiant': str(etudiant.pk)})
        self.assertRedirects(response, reverse('examen_fiche',
                                               kwargs={'pk': self.examen.pk}))
        self.assertEqual(self.examen.inscriptions.count(), 1)

    def test_inscrire_tous_les_restants(self):
        self._connexion()
        self._creer_etudiants(3)
        self._inscriptions(1)
        response = self.client.post(
            reverse('examen_inscrire_tous', kwargs={'pk': self.examen.pk}))
        self.assertRedirects(response, reverse('examen_fiche',
                                               kwargs={'pk': self.examen.pk}))
        self.assertEqual(self.examen.inscriptions.count(), 3)

    def test_desinscrire_un_etudiant(self):
        self._connexion()
        self._creer_etudiants(2)
        self._inscriptions(2)
        inscription = Inscription.objects.first()
        response = self.client.post(
            reverse('examen_desinscrire',
                    kwargs={'pk': self.examen.pk,
                            'inscription_pk': inscription.pk}))
        self.assertRedirects(response, reverse('examen_fiche',
                                               kwargs={'pk': self.examen.pk}))
        self.assertEqual(self.examen.inscriptions.count(), 1)

    def test_actions_anonymes_redirigent_vers_login(self):
        response = self.client.post(
            reverse('examen_inscrire_tous', kwargs={'pk': self.examen.pk}))
        self.assertIn('/login/', response.url)


class PaginationTests(TestCase):
    """SafePaginationMixin : page hors bornes -> redirection."""

    def test_page_hors_bornes_redirige_vers_derniere_page(self):
        Promotion.objects.create(nom='L3 INFO A')
        for i in range(30):  # 30 > paginate_by=25 -> 2 pages
            Enseignant.objects.create(
                noms=f'NOM{i} P',
                email=f'ens{i}@example.com')
        url = reverse('enseignant_list')
        response = self.client.get(url, {'page': 99})
        self.assertEqual(response.status_code, 302)
        self.assertIn('page=2', response.url)
        # Page valide : 200
        response = self.client.get(url, {'page': 1})
        self.assertEqual(response.status_code, 200)
class AuthAccessTests(TestCase):
    """Les vues en écriture exigent une session authentifiée."""

    @classmethod
    def setUpTestData(cls):
        cls.promotion = Promotion.objects.create(nom='L3 INFO A')
        cls.enseignant = Enseignant.objects.create(
            noms='KABISAYI TRESOR',
            email='t.kabisayi@example.com')
        cls.cours = Cours.objects.create(
            nom='Algorithmique', coefficient=1,
            enseignant=cls.enseignant, promotion=cls.promotion)
        cls.session = Session.objects.create(
            nom='SESSION 1', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        cls.examen = Examen.objects.create(
            cours=cls.cours, session=cls.session,
            date_examen=timezone.make_aware(datetime(2026, 1, 15, 8, 0)))
        cls.etudiant = Etudiant.objects.create(
            noms='DUPONT Jean',
            email='j.dupont@example.com',
            numero_etudiant='L3INFOA-900',
            promotion=cls.promotion)

    def test_pages_de_lecture_anonymes_accessibles(self):
        for url in [reverse('dashboard'), reverse('etudiant_list'),
                    reverse('examen_list')]:
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_dashboard_affiche_nouveaux_blocs(self):
        """Le dashboard enrichi affiche héros, compteurs, prochains examens et sessions."""
        from datetime import timedelta
        self.examen.date_examen = timezone.now() + timedelta(days=7)
        self.examen.save(update_fields=['date_examen'])
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tableau de Bord')
        self.assertContains(response, 'Prochains examens')
        self.assertContains(response, 'Sessions récentes')
        self.assertContains(response, 'Actions rapides')
        self.assertContains(response, 'Promotions')
        self.assertContains(response, 'Examens programmés')
        # L'examen repoussé dans le futur apparaît dans « Prochains examens »
        self.assertContains(response, 'Algorithmique')

    def test_pages_en_ecriture_anonymes_redirigent(self):
        urls = [
            reverse('etudiant_create'),
            reverse('etudiant_update', kwargs={'pk': self.etudiant.pk}),
            reverse('etudiant_delete', kwargs={'pk': self.etudiant.pk}),
            reverse('promotion_create'),
            reverse('promotion_update', kwargs={'pk': self.promotion.pk}),
            reverse('promotion_delete', kwargs={'pk': self.promotion.pk}),
            reverse('enseignant_create'),
            reverse('enseignant_update', kwargs={'pk': self.enseignant.pk}),
            reverse('enseignant_delete', kwargs={'pk': self.enseignant.pk}),
            reverse('session_create'),
            reverse('cours_create'),
            reverse('cours_update', kwargs={'pk': self.cours.pk}),
            reverse('cours_delete', kwargs={'pk': self.cours.pk}),
            reverse('examen_create'),
            reverse('examen_update', kwargs={'pk': self.examen.pk}),
            reverse('examen_delete', kwargs={'pk': self.examen.pk}),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302,
                             f'{url} devrait rediriger (302), obtenu '
                             f'{response.status_code}')
            self.assertIn('/login/', response.url, url)

    def test_login_page_repond(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_login_puis_acces_ecriture(self):
        user = User.objects.create_user('admin', 'admin@example.com',
                                        'pass12345')
        self.client.login(username='admin', password='pass12345')
        response = self.client.get(
            reverse('etudiant_update', kwargs={'pk': self.etudiant.pk}))
        self.assertEqual(response.status_code, 200)
class ExamenListFilterTests(TestCase):
    """Recherche et filtres de la liste des examens."""

    @classmethod
    def setUpTestData(cls):
        cls.promo1 = Promotion.objects.create(nom='L3 INFO A')
        cls.promo2 = Promotion.objects.create(nom='L3 SCF')
        cls.ens1 = Enseignant.objects.create(
            noms='KABISAYI TRESOR',
            email='filter.ens1@example.com')
        cls.ens2 = Enseignant.objects.create(
            noms='MUJINGA MAGUY',
            email='filter.ens2@example.com')
        cls.cours1 = Cours.objects.create(
            nom='Langage de programmation mobile', coefficient=1,
            enseignant=cls.ens1, promotion=cls.promo1)
        cls.cours2 = Cours.objects.create(
            nom='Ethique & Deontologie', coefficient=1,
            enseignant=cls.ens2, promotion=cls.promo2)
        cls.sess1 = Session.objects.create(
            nom='SESSION 1', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        cls.sess2 = Session.objects.create(
            nom='RATTRAPAGE SEMESTRE 2', semestre=2,
            type_session='rattrapage',
            date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 15))
        cls.ex1 = Examen.objects.create(
            cours=cls.cours1, session=cls.sess1,
            date_examen=timezone.make_aware(datetime(2026, 1, 20, 8, 0)))
        cls.ex2 = Examen.objects.create(
            cours=cls.cours2, session=cls.sess2,
            date_examen=timezone.make_aware(datetime(2026, 6, 5, 8, 0)))

    def _liste(self, params):
        return self.client.get(reverse('examen_list'), params)

    def test_recherche_par_cours(self):
        response = self._liste({'q': 'mobile'})
        self.assertEqual(response.status_code, 200)
        contenu = response.content.decode()
        self.assertIn('Langage de programmation mobile', contenu)
        self.assertNotIn('Ethique & Deontologie', contenu)

    def test_recherche_par_enseignant(self):
        response = self._liste({'q': 'KABISAYI'})
        contenu = response.content.decode()
        self.assertIn('Langage de programmation mobile', contenu)
        self.assertNotIn('Ethique & Deontologie', contenu)

    def test_cours_affiche_enseignant_entre_parentheses(self):
        """La colonne Cours affiche le nom de l'enseignant entre parenthèses."""
        response = self._liste({})
        self.assertEqual(response.status_code, 200)
        contenu = response.content.decode()
        self.assertIn('Langage de programmation mobile (KABISAYI TRESOR)', contenu)
        self.assertIn('Ethique &amp; Deontologie (MUJINGA MAGUY)', contenu)

    def test_cours_sans_enseignant_sans_parentheses(self):
        cours = Cours.objects.create(
            nom='Cours sans titulaire', coefficient=1, promotion=self.promo1)
        Examen.objects.create(
            cours=cours, session=self.sess1,
            date_examen=timezone.make_aware(datetime(2026, 2, 2, 8, 0)))
        response = self._liste({})
        contenu = response.content.decode()
        self.assertIn('Cours sans titulaire', contenu)
        self.assertNotIn('Cours sans titulaire (', contenu)

    def test_date_sans_heure(self):
        """La colonne Date n'affiche que la date, jamais l'heure."""
        response = self._liste({})
        contenu = response.content.decode()
        self.assertIn('20/01/2026', contenu)
        self.assertIn('05/06/2026', contenu)
        self.assertNotIn('20/01/2026 08', contenu)
        self.assertNotIn('05/06/2026 08', contenu)

    def test_filtre_promotion(self):
        response = self._liste({'promotion': str(self.promo1.pk)})
        contenu = response.content.decode()
        self.assertIn('Langage de programmation mobile', contenu)
        self.assertNotIn('Ethique & Deontologie', contenu)

    def test_filtre_session(self):
        response = self._liste({'session': str(self.sess2.pk)})
        contenu = response.content.decode()
        self.assertIn('Ethique &amp; Deontologie', contenu)
        self.assertNotIn('Langage de programmation mobile', contenu)

    def test_aucun_resultat_message(self):
        response = self._liste({'q': 'zzzz'})
        contenu = response.content.decode()
        self.assertIn('Aucun examen ne correspond aux filtres', contenu)

    def test_reinitialiser_les_filtres_affiche_tout(self):
        response = self._liste({'q': 'mobile'})
        contenu = response.content.decode()
        self.assertIn('R\u00e9initialiser les filtres', contenu)
        response = self._liste({})
        contenu = response.content.decode()
        self.assertIn('Langage de programmation mobile', contenu)
        self.assertIn('Ethique &amp; Deontologie', contenu)

    def test_filtres_conserves_dans_les_liens_de_pagination(self):
        # 30 examens -> 2 pages ; le filtre doit être conservé dans ?page=2.
        # Un (cours, session) étant unique, on crée des couples distincts.
        for i in range(30):
            cours = Cours.objects.create(
                nom=f'Cours pagination {i:02d}', coefficient=1,
                enseignant=self.ens1, promotion=self.promo1)
            Examen.objects.create(
                cours=cours, session=self.sess1,
                date_examen=timezone.make_aware(datetime(2026, 3, 1, 9, 0)))
        response = self._liste({'promotion': str(self.promo1.pk)})
        contenu = response.content.decode()
        self.assertIn('?page=2&amp;promotion=', contenu)

    def test_tri_par_defaut_par_cours(self):
        # « Ethique… » (cours2) doit précéder « Langage… » (cours1).
        examens = list(self._liste({}).context['examens'])
        self.assertEqual([e.pk for e in examens], [self.ex2.pk, self.ex1.pk])

    def test_tri_par_cours_inverse_et_conserve_les_filtres(self):
        response = self._liste({'tri': '-cours', 'q': 'mobile'})
        contenu = response.content.decode()
        self.assertIn('Langage de programmation mobile', contenu)
        # Le lien de tri vers l'ordre croissant conserve le filtre courant.
        self.assertIn('?tri=cours&q=mobile', contenu)

    def test_tri_date_restaure_ancien_ordre(self):
        # Tri chronologique : ex1 (janvier) avant ex2 (juin).
        examens = list(self._liste({'tri': 'date'}).context['examens'])
        self.assertEqual([e.pk for e in examens], [self.ex1.pk, self.ex2.pk])

    def test_tri_invalide_retombe_sur_cours(self):
        examens = list(self._liste({'tri': 'pirate'}).context['examens'])
        self.assertEqual([e.pk for e in examens], [self.ex2.pk, self.ex1.pk])

    def test_unicite_cours_session(self):
        # Un 2e examen avec le même (cours, session) viole la contrainte.
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Examen.objects.create(
                cours=self.cours1, session=self.sess1,
                date_examen=timezone.make_aware(datetime(2026, 1, 25, 8, 0)))

    def test_unicite_autorise_meme_cours_autre_session(self):
        examen = Examen.objects.create(
            cours=self.cours1, session=self.sess2,
            date_examen=timezone.make_aware(datetime(2026, 6, 5, 10, 0)))
        self.assertIsNotNone(examen.pk)

    def test_stats_en_tete(self):
        """Les cartes d'en-tête affichent total, notés, en attente et à venir."""
        from datetime import timedelta
        self.ex1.est_note = True
        self.ex1.save(update_fields=['est_note'])
        Examen.objects.create(
            cours=self.cours1, session=self.sess2,
            date_examen=timezone.now() + timedelta(days=30))
        response = self._liste({})
        self.assertEqual(response.status_code, 200)
        stats = response.context['stats_examens']
        self.assertEqual(stats['total'], 3)
        self.assertEqual(stats['notes'], 1)
        self.assertEqual(stats['en_attente'], 2)
        self.assertEqual(stats['a_venir'], 1)
        contenu = response.content.decode()
        for mot in ('Examens', 'Notés', 'En attente', 'À venir'):
            self.assertIn(mot, contenu)
        # Les cartes pointent vers la liste, préservant l'ergonomie.
        self.assertIn('?statut=note', contenu)
        self.assertIn('?statut=non-note', contenu)
        self.assertIn('?statut=a-venir', contenu)

    def test_filtre_a_venir(self):
        """« ?statut=a-venir » ne garde que les examens dont la date est à venir."""
        from datetime import timedelta
        cours_futur = Cours.objects.create(
            nom='Cours à venir', coefficient=1,
            enseignant=self.ens1, promotion=self.promo1)
        Examen.objects.create(
            cours=cours_futur, session=self.sess2,
            date_examen=timezone.now() + timedelta(days=30))
        response = self._liste({'statut': 'a-venir'})
        self.assertEqual(response.status_code, 200)
        contenu = response.content.decode()
        # Seul l'examen futur subsiste (ex1 et ex2 sont passés).
        self.assertIn('1 examen(s) sur 3 correspondent aux filtres', contenu)
        self.assertIn('Cours à venir', contenu)
        self.assertNotIn('Langage de programmation mobile', contenu)
        self.assertNotIn('Ethique &amp; Deontologie', contenu)
        # ... et le filtre reste sélectionné dans la barre de filtres.
        self.assertIn('selected>À venir', contenu)
        self.assertEqual(response.context['filters']['statut'], 'a-venir')

    def test_filtre_statut(self):
        """« ?statut=note » ne garde que les examens marqués comme notés."""
        self.ex1.est_note = True
        self.ex1.save(update_fields=['est_note'])
        response = self._liste({'statut': 'note'})
        contenu = response.content.decode()
        self.assertIn('Langage de programmation mobile', contenu)
        self.assertNotIn('Ethique &amp; Deontologie', contenu)
        # L'inverse exclut l'examen noté.
        response = self._liste({'statut': 'non-note'})
        contenu = response.content.decode()
        self.assertIn('Ethique &amp; Deontologie', contenu)
        self.assertNotIn('Langage de programmation mobile', contenu)


class ExamenNoteTests(BaseDataMixin, TestCase):
    """Bascule « examen noté » depuis la liste des examens (/examens/)."""

    def test_par_defaut_non_note(self):
        self.assertFalse(Examen.objects.get(pk=self.examen.pk).est_note)

    def test_anonyme_redirige_vers_login(self):
        response = self.client.post(
            reverse('examen_notee', kwargs={'pk': self.examen.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_marquer_puis_demarquer(self):
        self._connexion()
        url = reverse('examen_notee', kwargs={'pk': self.examen.pk})
        response = self.client.post(url)
        self.assertRedirects(response, reverse('examen_list'))
        self.assertTrue(Examen.objects.get(pk=self.examen.pk).est_note)
        # Second POST : on retire la mention.
        self.client.post(url)
        self.assertFalse(Examen.objects.get(pk=self.examen.pk).est_note)

    def test_get_ne_modifie_pas_mais_redirige(self):
        self._connexion()
        response = self.client.get(
            reverse('examen_notee', kwargs={'pk': self.examen.pk}))
        self.assertRedirects(response, reverse('examen_list'))
        self.assertFalse(Examen.objects.get(pk=self.examen.pk).est_note)

    def test_next_preserve_filtres_tri_et_page(self):
        self._connexion()
        url = reverse('examen_notee', kwargs={'pk': self.examen.pk})
        cible = '/examens/?tri=cours&page=2'
        response = self.client.post(url, {'next': cible})
        self.assertRedirects(response, cible, fetch_redirect_response=False)
        self.assertTrue(Examen.objects.get(pk=self.examen.pk).est_note)

    def test_next_externe_ignore(self):
        """Une valeur de « next » externe ne doit pas produire de redirection ouverte."""
        self._connexion()
        url = reverse('examen_notee', kwargs={'pk': self.examen.pk})
        response = self.client.post(url, {'next': 'https://example.com/'})
        self.assertRedirects(response, reverse('examen_list'))

    def test_inexistant_renvoie_404(self):
        self._connexion()
        response = self.client.post(
            reverse('examen_notee', kwargs={'pk': 9999}))
        self.assertEqual(response.status_code, 404)

    def test_liste_affiche_badge_note(self):
        self.examen.est_note = True
        self.examen.save(update_fields=['est_note'])
        response = self.client.get(reverse('examen_list'))
        self.assertEqual(response.status_code, 200)
        contenu = response.content.decode()
        self.assertIn('Noté', contenu)
        self.assertIn('Annuler noté', contenu)

    def test_liste_affiche_badge_non_note(self):
        response = self.client.get(reverse('examen_list'))
        self.assertEqual(response.status_code, 200)
        contenu = response.content.decode()
        self.assertIn('Non noté', contenu)
        self.assertIn('Marquer noté', contenu)


class ExamenImpressionTests(BaseDataMixin, TestCase):
    """Page imprimable de la liste des cours (examens) non notés."""

    def _examen_supplementaire(self):
        session2 = Session.objects.create(
            nom='SESSION RATTRAPAGE', semestre=1, type_session='rattrapage',
            date_debut=date(2026, 8, 1), date_fin=date(2026, 8, 15))
        return Examen.objects.create(
            cours=self.cours, session=session2,
            date_examen=timezone.make_aware(datetime(2026, 8, 10, 9, 0)))

    def test_page_accessible_sans_connexion(self):
        reponse = self.client.get(reverse('examen_non_notes_print'))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, 'Liste des cours non notés')

    def test_bouton_present_sur_la_liste_des_examens(self):
        reponse = self.client.get(reverse('examen_list'))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, 'Imprimer les non-notés')
        self.assertContains(reponse, reverse('examen_non_notes_print'))

    def test_ne_liste_que_les_non_notes(self):
        self.examen.est_note = True
        self.examen.save(update_fields=['est_note'])
        autre = self._examen_supplementaire()
        reponse = self.client.get(reverse('examen_non_notes_print'))
        self.assertEqual(reponse.status_code, 200)
        examens = list(reponse.context['examens'])
        self.assertEqual([e.pk for e in examens], [autre.pk])
        self.assertContains(reponse, 'Nombre total : <strong>1</strong>')

    def test_total_et_colonnes(self):
        reponse = self.client.get(reverse('examen_non_notes_print'))
        contenu = reponse.content.decode()
        for colonne in ('Cours', 'Promotion', 'Enseignant', 'Date',
                        'Nombre total'):
            self.assertIn(colonne, contenu)
        self.assertIn('window.print()', contenu)

    def test_sans_session_ni_heure(self):
        """La session est retirée du tableau et la date est affichée sans l'heure."""
        autre = self._examen_supplementaire()
        reponse = self.client.get(reverse('examen_non_notes_print'))
        contenu = reponse.content.decode()
        # La colonne « Session » n'existe plus : aucun nom de session n'apparaît.
        self.assertNotIn('RATTRAPAGE', contenu)
        # L'examen supplémentaire est daté du 10/08/2026 à 09h00 :
        # seule la date doit apparaître, jamais l'heure.
        self.assertIn('10/08/2026', contenu)
        self.assertNotIn('09:00', contenu)
        self.assertNotIn('08:00', contenu)

    def test_pas_de_colonne_local(self):
        """Aucun local (salle) ne doit être imprimé, même renseigné en base."""
        reponse = self.client.get(reverse('examen_non_notes_print'))
        contenu = reponse.content.decode()
        # self.examen porte « Local 1 » : il ne doit plus apparaître.
        self.assertNotIn('Local 1', contenu)

    def test_signatures_du_jury(self):
        reponse = self.client.get(reverse('examen_non_notes_print'))
        contenu = reponse.content.decode()
        self.assertIn('Le Secrétaire du Jury', contenu)
        self.assertIn('Le Président du Jury', contenu)

    def test_trie_par_enseignant(self):
        """La liste imprimée est triée par noms d'enseignant."""
        for nom, prenom, email in [
                ('ZZZ', 'Ulysse', 'z.ulysse@example.com'),
                ('AAA', 'Premier', 'a.premier@example.com')]:
            ens = Enseignant.objects.create(
                noms=f'{nom} {prenom}', email=email)
            cours = Cours.objects.create(
                nom=f'Cours de {nom}', coefficient=1,
                enseignant=ens, promotion=self.promotion)
            Examen.objects.create(
                cours=cours, session=self.session,
                date_examen=timezone.make_aware(datetime(2026, 5, 1, 8, 0)))
        reponse = self.client.get(reverse('examen_non_notes_print'))
        self.assertEqual(reponse.status_code, 200)
        examens = list(reponse.context['examens'])
        self.assertEqual(len(examens), 3)  # examen de base + les deux ajoutés
        # L'examen de base est rattaché à l'enseignant KABISAYI TRESOR.
        noms = [e.cours.enseignant.noms for e in examens]
        self.assertEqual(noms, ['AAA Premier', 'KABISAYI TRESOR', 'ZZZ Ulysse'])

    def test_cours_sans_enseignant_en_dernier(self):
        """Les cours sans titulaire sont rejetés en fin de liste."""
        cours = Cours.objects.create(
            nom='Cours sans titulaire', coefficient=1,
            promotion=self.promotion)
        Examen.objects.create(
            cours=cours, session=self.session,
            date_examen=timezone.make_aware(datetime(2026, 5, 2, 8, 0)))
        reponse = self.client.get(reverse('examen_non_notes_print'))
        examens = list(reponse.context['examens'])
        # Seul l'examen de base (KABISAYI) le précède.
        self.assertEqual(examens[-1].cours.nom, 'Cours sans titulaire')
        self.assertIsNone(examens[-1].cours.enseignant)


class DashboardSessionActiveTests(TestCase):
    """Les statistiques du tableau de bord sont liées à la session en cours."""

    @classmethod
    def setUpTestData(cls):
        cls.sess_inactive = Session.objects.create(
            nom='SESSION ANCIENNE', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        cls.sess_active = Session.objects.create(
            nom='SESSION EN COURS', semestre=2, type_session='normale',
            date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 20),
            est_active=True)
        cls.promo1 = Promotion.objects.create(nom='L1')
        cls.promo2 = Promotion.objects.create(nom='L2')
        cls.ens1 = Enseignant.objects.create(
            noms='KARIM Ali', email='a.karim@example.com')
        cls.ens2 = Enseignant.objects.create(
            noms='LAMBER Benoit', email='b.lamber@example.com')
        cls.cours1 = Cours.objects.create(
            nom='Cours ancien', coefficient=1,
            enseignant=cls.ens1, promotion=cls.promo1)
        cls.cours2 = Cours.objects.create(
            nom='Cours actif', coefficient=1,
            enseignant=cls.ens2, promotion=cls.promo2)
        cls.ex1 = Examen.objects.create(
            cours=cls.cours1, session=cls.sess_inactive,
            date_examen=timezone.make_aware(datetime(2026, 1, 15, 8, 0)))
        cls.ex2 = Examen.objects.create(
            cours=cls.cours2, session=cls.sess_active,
            date_examen=timezone.make_aware(datetime(2026, 6, 10, 8, 0)))
        cls.etu1 = Etudiant.objects.create(
            noms='NDAYE Jean',
            email='j.ndaye@example.com',
            numero_etudiant='L1-001', promotion=cls.promo1)
        cls.etu2 = Etudiant.objects.create(
            noms='KAMANDA Marie',
            email='m.kamanda@example.com',
            numero_etudiant='L2-001', promotion=cls.promo2)
        Inscription.objects.create(examen=cls.ex1, etudiant=cls.etu1)
        Inscription.objects.create(examen=cls.ex2, etudiant=cls.etu2)

    def setUp(self):
        cache.clear()  # isole chaque test du cache des compteurs

    def test_compteurs_limites_a_la_session_active(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual(ctx['total_examens'], 1)      # ex2 seulement
        self.assertEqual(ctx['total_cours'], 1)        # cours2
        self.assertEqual(ctx['total_enseignants'], 1)  # ens2
        self.assertEqual(ctx['total_promotions'], 1)   # promo2
        self.assertEqual(ctx['total_etudiants'], 1)    # etu2
        self.assertEqual(ctx['total_sessions'], 1)     # la session en cours

    def test_dashboard_affiche_session_en_cours(self):
        response = self.client.get(reverse('dashboard'))
        contenu = response.content.decode()
        self.assertIn('Session en cours : SESSION EN COURS', contenu)
        # La carte Sessions affiche « Session en cours ».
        self.assertIn('Session en cours', contenu)

    def test_alerte_et_totaux_globaux_sans_session_active(self):
        Session.objects.filter(est_active=True).update(est_active=False)
        cache.clear()
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual(ctx['total_examens'], 2)
        self.assertEqual(ctx['total_cours'], 2)
        self.assertEqual(ctx['total_sessions'], 2)
        contenu = response.content.decode()
        self.assertIn('Aucune session en cours', contenu)
        self.assertNotIn('Session en cours : SESSION EN COURS', contenu)


class ExamensSessionEnCoursTests(TestCase):
    """La liste des examens est limitée, par défaut, à la session en cours."""

    @classmethod
    def setUpTestData(cls):
        cls.sess_inactive = Session.objects.create(
            nom='SESSION ANCIENNE', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        cls.sess_active = Session.objects.create(
            nom='SESSION EN COURS', semestre=2, type_session='normale',
            date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 20),
            est_active=True)
        cls.promo = Promotion.objects.create(nom='L3 SCF')
        cls.ens = Enseignant.objects.create(
            noms='KALALA Paul', email='p.kalala@example.com')
        cls.cours_actif = Cours.objects.create(
            nom='Cours actif', coefficient=1,
            enseignant=cls.ens, promotion=cls.promo)
        cls.cours_ancien = Cours.objects.create(
            nom='Cours ancien', coefficient=1,
            enseignant=cls.ens, promotion=cls.promo)
        cls.ex_active = Examen.objects.create(
            cours=cls.cours_actif, session=cls.sess_active,
            date_examen=timezone.make_aware(datetime(2026, 6, 10, 8, 0)))
        cls.ex_inactive = Examen.objects.create(
            cours=cls.cours_ancien, session=cls.sess_inactive,
            date_examen=timezone.make_aware(datetime(2026, 1, 15, 8, 0)))

    def _get(self, params=None):
        return self.client.get(reverse('examen_list'), params or {})

    def test_defaut_limite_a_la_session_active(self):
        response = self._get()
        ctx = response.context
        self.assertEqual(list(ctx['examens']), [self.ex_active])
        self.assertTrue(ctx['session_defaut'])
        self.assertEqual(ctx['session_active'], self.sess_active)
        contenu = response.content.decode()
        self.assertIn('session en cours', contenu)
        self.assertIn('Cours actif', contenu)
        self.assertNotIn('Cours ancien', contenu)
        self.assertIn('selected>Session en cours', contenu)

    def test_toutes_les_sessions(self):
        response = self._get({'session': '0'})
        ctx = response.context
        self.assertEqual(
            {e.pk for e in ctx['examens']},
            {self.ex_active.pk, self.ex_inactive.pk})
        self.assertFalse(ctx['session_defaut'])
        self.assertIn('Cours ancien', response.content.decode())

    def test_filtre_session_par_pk(self):
        response = self._get({'session': str(self.sess_inactive.pk)})
        ctx = response.context
        self.assertEqual(list(ctx['examens']), [self.ex_inactive])
        self.assertFalse(ctx['session_defaut'])
        self.assertFalse(ctx['session_choisie'])

    def test_en_cours_explicite(self):
        response = self._get({'session': 'en-cours'})
        ctx = response.context
        self.assertEqual(list(ctx['examens']), [self.ex_active])
        self.assertTrue(ctx['session_choisie'])
        self.assertFalse(ctx['session_defaut'])
        self.assertIn('selected>Session en cours', response.content.decode())

    def test_sans_session_active_tout_est_affiche(self):
        Session.objects.filter(est_active=True).update(est_active=False)
        response = self._get()
        ctx = response.context
        self.assertEqual(
            {e.pk for e in ctx['examens']},
            {self.ex_active.pk, self.ex_inactive.pk})
        self.assertFalse(ctx['session_defaut'])
        self.assertIsNone(ctx['session_active'])


class SessionActiveTests(TestCase):
    """Définition de la « session en cours » (une seule active à la fois)."""

    @classmethod
    def setUpTestData(cls):
        cls.s1 = Session.objects.create(
            nom='SESSION 1', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        cls.s2 = Session.objects.create(
            nom='SESSION 2', semestre=2, type_session='normale',
            date_debut=date(2026, 6, 1), date_fin=date(2026, 7, 5),
            est_active=True)
        cls.user = User.objects.create_user(
            'admin', 'admin@example.com', 'pass12345')

    def _connexion(self):
        self.client.force_login(self.user)

    def test_une_seule_session_active(self):
        s3 = Session.objects.create(
            nom='SESSION 3', semestre=1, type_session='rattrapage',
            date_debut=date(2026, 8, 1), date_fin=date(2026, 8, 15),
            est_active=True)
        self.s2.refresh_from_db()
        self.assertFalse(self.s2.est_active)
        self.assertTrue(s3.est_active)
        self.assertEqual(Session.objects.filter(est_active=True).count(), 1)

    def test_defaut_inactif(self):
        session = Session.objects.create(
            nom='SESSION 4', semestre=2, type_session='normale',
            date_debut=date(2026, 9, 1), date_fin=date(2026, 9, 15))
        self.assertFalse(session.est_active)

    def test_vue_bascule_active(self):
        self._connexion()
        response = self.client.post(
            reverse('session_active', kwargs={'pk': self.s1.pk}))
        self.assertRedirects(response, reverse('session_list'))
        self.s1.refresh_from_db()
        self.s2.refresh_from_db()
        self.assertTrue(self.s1.est_active)
        self.assertFalse(self.s2.est_active)

    def test_vue_annule(self):
        self._connexion()
        self.client.post(reverse('session_active', kwargs={'pk': self.s2.pk}))
        self.s2.refresh_from_db()
        self.assertFalse(self.s2.est_active)

    def test_anonyme_redirige_vers_login(self):
        response = self.client.post(
            reverse('session_active', kwargs={'pk': self.s1.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_inexistant_renvoie_404(self):
        self._connexion()
        response = self.client.post(
            reverse('session_active', kwargs={'pk': 9999}))
        self.assertEqual(response.status_code, 404)

    def test_liste_affiche_badge_session_en_cours(self):
        response = self.client.get(reverse('session_list'))
        contenu = response.content.decode()
        self.assertIn('Session en cours', contenu)
        self.assertIn('Définir en cours', contenu)
        self.assertIn('Annuler', contenu)

    def test_formulaire_examen_preselectionne_session_active(self):
        form = ExamenForm()
        self.assertEqual(form.fields['session'].initial, self.s2.pk)

    def test_formulaire_examen_edition_preserve_session(self):
        promotion = Promotion.objects.create(nom='L3 SCF')
        cours = Cours.objects.create(
            nom='Mathématiques', coefficient=1, promotion=promotion)
        examen = Examen.objects.create(
            cours=cours, session=self.s1,
            date_examen=timezone.make_aware(datetime(2026, 1, 15, 8, 0)))
        form = ExamenForm(instance=examen)
        # En édition, on conserve la session déjà attachée à l'examen :
        # la valeur vient de l'instance, pas de la session active.
        self.assertEqual(form.initial['session'], self.s1.pk)


class MergePromotionsCommandTest(TestCase):
    """Vérifie la fusion de promotions (ex. « L2 SD A » + « L2 TS A » → « L2 SDA »)."""

    def setUp(self):
        self.enseignant = Enseignant.objects.create(
            noms='ENSEIGNANT TEST',
            email='enseignant.merge@example.com')
        self.promo_a = Promotion.objects.create(nom='L2 SD A')
        self.promo_b = Promotion.objects.create(nom='L2 TS A')

    def _etudiant(self, promo, i):
        return Etudiant.objects.create(
            noms=f'ETU{i} Prenom{i}',
            email=f'merge{i}@example.com',
            numero_etudiant=f'L2SDA-{i:03d}', promotion=promo)

    def _cours(self, promo, nom):
        return Cours.objects.create(
            nom=nom, coefficient=1, enseignant=self.enseignant,
            promotion=promo)

    def test_fusion_regroupe_etudiants_et_cours(self):
        self._etudiant(self.promo_a, 1)
        self._etudiant(self.promo_a, 2)
        self._etudiant(self.promo_b, 3)
        self._cours(self.promo_a, 'ANGLAIS')
        self._cours(self.promo_a, 'ARCHIVAGE 2')
        self._cours(self.promo_b, 'STENO')

        call_command('merge_promotions',
                     target='L2 SDA',
                     sources=['L2 SD A', 'L2 TS A'])

        cible = Promotion.objects.get(nom='L2 SDA')
        self.assertEqual(Etudiant.objects.filter(promotion=cible).count(), 3)
        self.assertEqual(Cours.objects.filter(promotion=cible).count(), 3)
        self.assertFalse(Promotion.objects.filter(nom__in=['L2 SD A', 'L2 TS A']).exists())

    def test_fusion_idempotente_si_cible_existe_deja(self):
        cible = Promotion.objects.create(nom='L2 SDA')
        self._etudiant(self.promo_a, 1)
        self._etudiant(self.promo_b, 2)

        call_command('merge_promotions',
                     target='L2 SDA',
                     sources=['L2 SD A', 'L2 TS A'])

        self.assertEqual(Promotion.objects.get(nom='L2 SDA').pk, cible.pk)
        self.assertEqual(Promotion.objects.filter(nom__in=['L2 SD A', 'L2 TS A']).count(), 0)
        self.assertEqual(Etudiant.objects.filter(promotion__nom='L2 SDA').count(), 2)

    def test_fusion_cours_homonymes_et_examens(self):
        """Un cours présent dans la source ET la cible doit être fusionné.

        Sans cette fusion, la contrainte d'unicité (promotion, nom) ferait
        échouer le déplacement groupé vers la promotion cible.
        """
        cible = Promotion.objects.create(nom='L2 SDA')
        # Cours homonyme des deux côtés + un cours propre à chaque côté.
        cours_cible = self._cours(cible, 'ANGLAIS')
        cours_source = self._cours(self.promo_a, 'ANGLAIS')
        self._cours(self.promo_a, 'STENO')
        self._cours(cible, 'ARCHIVAGE 2')

        session = Session.objects.create(
            nom='S1', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 1, 20))
        etu_source = self._etudiant(self.promo_a, 1)
        etu_cible = self._etudiant(cible, 2)
        # Même session pour les deux examens : les inscriptions fusionnent.
        examen_cible = Examen.objects.create(
            cours=cours_cible, session=session,
            date_examen=timezone.make_aware(datetime(2026, 1, 6, 8, 0)))
        examen_source = Examen.objects.create(
            cours=cours_source, session=session,
            date_examen=timezone.make_aware(datetime(2026, 1, 6, 10, 0)))
        Inscription.objects.create(examen=examen_source, etudiant=etu_source)
        Inscription.objects.create(examen=examen_cible, etudiant=etu_cible)

        call_command('merge_promotions',
                     target='L2 SDA',
                     sources=['L2 SD A', 'L2 TS A'])

        # Un seul cours « ANGLAIS » subsiste, dans la promotion cible.
        cible = Promotion.objects.get(nom='L2 SDA')
        self.assertEqual(Cours.objects.filter(nom='ANGLAIS').count(), 1)
        self.assertEqual(
            Cours.objects.filter(promotion=cible, nom='ANGLAIS').count(), 1)
        # Un seul examen (session identique) et aucune inscription perdue.
        self.assertEqual(
            Examen.objects.filter(cours__nom='ANGLAIS').count(), 1)
        examen = Examen.objects.get(cours__nom='ANGLAIS')
        self.assertEqual(examen.inscriptions.count(), 2)
        # Les cours non homonymes ont bien suivi le déplacement.
        self.assertEqual(
            Cours.objects.filter(promotion=cible).count(), 3)

    def test_fusion_cours_homonyme_sessions_differentes(self):
        """Des examens sur des sessions distinctes sont conservés séparément."""
        cible = Promotion.objects.create(nom='L2 SDA')
        cours_cible = self._cours(cible, 'ANGLAIS')
        cours_source = self._cours(self.promo_b, 'ANGLAIS')
        s1 = Session.objects.create(
            nom='S1', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 1, 20))
        s2 = Session.objects.create(
            nom='S2', semestre=2, type_session='normale',
            date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 15))
        Examen.objects.create(
            cours=cours_cible, session=s1,
            date_examen=timezone.make_aware(datetime(2026, 1, 6, 8, 0)))
        Examen.objects.create(
            cours=cours_source, session=s2,
            date_examen=timezone.make_aware(datetime(2026, 6, 2, 8, 0)))

        call_command('merge_promotions',
                     target='L2 SDA',
                     sources=['L2 TS A'])

        self.assertEqual(Cours.objects.filter(nom='ANGLAIS').count(), 1)
        self.assertEqual(
            Examen.objects.filter(cours__nom='ANGLAIS').count(), 2)
        self.assertEqual(
            Cours.objects.filter(promotion__nom='L2 SDA',
                                 nom='ANGLAIS').count(), 1)

    def test_dry_run_ne_modifie_pas_la_base(self):
        self._etudiant(self.promo_a, 1)
        self._cours(self.promo_a, 'ANGLAIS')

        call_command('merge_promotions',
                     target='L2 SDA',
                     sources=['L2 SD A'],
                     dry_run=True)

        self.assertTrue(Promotion.objects.filter(nom='L2 SD A').exists())
        self.assertFalse(Promotion.objects.filter(nom='L2 SDA').exists())
        self.assertEqual(Etudiant.objects.filter(promotion=self.promo_a).count(), 1)
        self.assertEqual(Cours.objects.filter(promotion=self.promo_a).count(), 1)
class SessionDetailViewTest(BaseDataMixin, TestCase):
    """Tests de la page de détail d'une session (examens + participants)."""

    def setUp(self):
        self.session = Session.objects.create(
            nom='Session SEMESTRE 1 2025 - 2026', semestre=1,
            type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        promo1 = Promotion.objects.create(nom='L1 INFO A')
        promo2 = Promotion.objects.create(nom='L1 SCF LMD')
        enseignant = Enseignant.objects.create(
            noms='KABISAYI TRESOR',
            email='session.detail@example.com')
        self.cours1 = Cours.objects.create(
            nom='Mathématiques', coefficient=1,
            enseignant=enseignant, promotion=promo1)
        self.cours2 = Cours.objects.create(
            nom='Anglais', coefficient=1,
            enseignant=enseignant, promotion=promo2)
        self.ex1 = Examen.objects.create(
            cours=self.cours1, session=self.session,
            date_examen=timezone.make_aware(datetime(2026, 1, 15, 8, 0)))
        self.ex2 = Examen.objects.create(
            cours=self.cours2, session=self.session,
            date_examen=timezone.make_aware(datetime(2026, 1, 16, 8, 0)))

    def _etudiant(self, noms, numero):
        return Etudiant.objects.create(
            noms=noms,
            email=f'{numero.lower()}@example.com',
            numero_etudiant=numero, promotion=self.ex1.cours.promotion)

    def test_liste_examens_et_participants(self):
        etu1 = self._etudiant('ASSANI LAZARINE', 'L1INFOA-001')
        etu2 = self._etudiant('MABANGI WAMABANGI', 'L1INFOA-002')
        Inscription.objects.create(examen=self.ex1, etudiant=etu1)
        Inscription.objects.create(examen=self.ex1, etudiant=etu2)
        # etu1 inscrit aussi à ex2 -> inscriptions = 3 mais participants uniques = 2
        Inscription.objects.create(examen=self.ex2, etudiant=etu1)

        response = self.client.get(reverse('session_detail', args=[self.session.pk]))
        self.assertEqual(response.status_code, 200)
        contenu = response.content.decode()
        self.assertIn('Session SEMESTRE 1 2025 - 2026', contenu)
        # Onglets Participants / Cours + bouton d'export PDF
        self.assertIn('nav-tabs', contenu)
        self.assertIn('id="tab-participants"', contenu)
        self.assertIn('id="tab-cours"', contenu)
        self.assertIn(reverse('session_participants_pdf',
                              args=[self.session.pk]), contenu)
        self.assertIn('Participants (2)', contenu)
        self.assertIn('Cours (2)', contenu)
        self.assertIn('Mathématiques', contenu)
        self.assertIn('Anglais', contenu)
        self.assertIn('L1INFOA-001', contenu)
        self.assertIn('L1INFOA-002', contenu)
        self.assertEqual(response.context['nb_inscriptions'], 3)
        self.assertEqual(response.context['nb_participants'], 2)
        self.assertEqual(len(response.context['participants']), 2)

    def test_session_sans_examen(self):
        session_vide = Session.objects.create(
            nom='Session vide', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        response = self.client.get(reverse('session_detail', args=[session_vide.pk]))
        self.assertEqual(response.status_code, 200)
        contenu = response.content.decode()
        self.assertIn('Aucun participant inscrit pour cette session.', contenu)
        self.assertIn('Aucun cours programmé pour cette session.', contenu)
        self.assertEqual(response.context['nb_participants'], 0)

    def test_export_pdf_participants(self):
        """L'export PDF liste les participants, regroupés par promotion."""
        from pypdf import PdfReader

        etu1 = self._etudiant('ASSANI LAZARINE', 'L1INFOA-001')
        etu2 = self._etudiant('MABANGI WAMABANGI', 'L1INFOA-002')
        Inscription.objects.create(examen=self.ex1, etudiant=etu1)
        Inscription.objects.create(examen=self.ex1, etudiant=etu2)
        Inscription.objects.create(examen=self.ex2, etudiant=etu1)

        # Anonyme -> redirection vers la page de connexion.
        url = reverse('session_participants_pdf', args=[self.session.pk])
        reponse_anonyme = self.client.get(url)
        self.assertEqual(reponse_anonyme.status_code, 302)

        self._connexion()
        reponse = self.client.get(url)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse['Content-Type'], 'application/pdf')
        self.assertIn('.pdf', reponse['Content-Disposition'])

        octets = b''.join(reponse.streaming_content)
        self.assertTrue(octets.startswith(b'%PDF'))
        texte = ''.join(
            page.extract_text() or '' for page in PdfReader(BytesIO(octets)).pages)
        self.assertIn('Liste des participants', texte)
        self.assertIn('ASSANI LAZARINE', texte)
        self.assertIn('MABANGI WAMABANGI', texte)
        self.assertIn('L1 INFO A', texte)
        self.assertNotIn('L1INFOA-001', texte)
        self.assertNotIn('L1INFOA-002', texte)

    def test_export_pdf_session_vide(self):
        """L'export PDF d'une session sans participant reste un PDF valide."""
        session_vide = Session.objects.create(
            nom='Session vide', semestre=1, type_session='normale',
            date_debut=date(2026, 1, 5), date_fin=date(2026, 2, 5))
        self._connexion()
        reponse = self.client.get(reverse(
            'session_participants_pdf', args=[session_vide.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse['Content-Type'], 'application/pdf')
        octets = b''.join(reponse.streaming_content)
        self.assertTrue(octets.startswith(b'%PDF'))


class EnseignantDetailViewTest(BaseDataMixin, TestCase):
    """Fiche détaillée d'un enseignant : cours, promotions et examens."""

    def test_detail_affiche_cours_promotions_et_examens(self):
        self._creer_etudiants(1)
        self._inscriptions(1)
        reponse = self.client.get(reverse(
            'enseignant_detail', args=[self.enseignant.pk]))
        self.assertEqual(reponse.status_code, 200)
        contenu = reponse.content.decode()
        self.assertIn('KABISAYI', contenu)
        self.assertIn('Langage de programmation mobile', contenu)
        self.assertIn('L3 INFO A', contenu)
        self.assertIn('SESSION 1, SEMESTRE 1 2025 - 2026', contenu)

    def test_detail_enseignant_sans_cours(self):
        enseignant = Enseignant.objects.create(
            noms='SANS COURS', email='sans.cours@example.com')
        reponse = self.client.get(reverse(
            'enseignant_detail', args=[enseignant.pk]))
        self.assertEqual(reponse.status_code, 200)
        contenu = reponse.content.decode()
        self.assertIn('Aucun cours attribué à cet enseignant.', contenu)
        self.assertIn('Aucune promotion concernée.', contenu)
        self.assertIn('Aucun examen programmé pour cet enseignant.', contenu)

    def test_detail_inexistant_renvoie_404(self):
        reponse = self.client.get(reverse('enseignant_detail', args=[9999]))
        self.assertEqual(reponse.status_code, 404)

    def test_liste_lien_vers_detail(self):
        reponse = self.client.get(reverse('enseignant_list'))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(
            reponse, reverse('enseignant_detail',
                             args=[self.enseignant.pk]))

    def test_promotion_detail_lien_vers_enseignant(self):
        reponse = self.client.get(reverse(
            'promotion_detail', args=[self.promotion.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(
            reponse, reverse('enseignant_detail',
                             args=[self.enseignant.pk]))


class EtudiantDetailViewTest(BaseDataMixin, TestCase):
    """Fiche détaillée d'un étudiant : inscriptions, notes et moyenne."""

    def test_detail_affiche_infos_et_notes(self):
        self._creer_etudiants(2)
        self._inscriptions(2)
        etudiant = Etudiant.objects.get(numero_etudiant='L3INFOA-000')
        for ins in Inscription.objects.filter(etudiant=etudiant):
            ins.note_interro = 3.5
            ins.note_tp = 4
            ins.note_examen = 6
            ins.save()
        reponse = self.client.get(reverse('etudiant_detail', args=[etudiant.pk]))
        self.assertEqual(reponse.status_code, 200)
        contenu = reponse.content.decode()
        self.assertIn('NOM0', contenu)
        self.assertIn('L3INFOA-000', contenu)
        self.assertIn('etu0@example.com', contenu)
        self.assertIn('Langage de programmation mobile', contenu)
        self.assertIn('SESSION 1, SEMESTRE 1 2025 - 2026', contenu)
        # Moyenne 13,50 affichée
        self.assertIn('13,50', contenu)

    def test_detail_sans_inscription(self):
        self._creer_etudiants(1)
        etudiant = Etudiant.objects.get(numero_etudiant='L3INFOA-000')
        reponse = self.client.get(reverse('etudiant_detail', args=[etudiant.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, 'Aucun examen pour cet étudiant.')

    def test_detail_inexistant_renvoie_404(self):
        reponse = self.client.get(reverse('etudiant_detail', args=[9999]))
        self.assertEqual(reponse.status_code, 404)

    def test_liste_lien_vers_detail(self):
        self._creer_etudiants(1)
        etudiant = Etudiant.objects.get(numero_etudiant='L3INFOA-000')
        reponse = self.client.get(reverse(
            'etudiant_list') + f'?promotion={self.promotion.pk}')
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, reverse('etudiant_detail', args=[etudiant.pk]))

    def test_promotion_detail_lien_vers_detail_etudiant(self):
        self._creer_etudiants(1)
        etudiant = Etudiant.objects.get(numero_etudiant='L3INFOA-000')
        reponse = self.client.get(reverse(
            'promotion_detail', args=[self.promotion.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, reverse('etudiant_detail', args=[etudiant.pk]))


class CoursDetailViewTest(BaseDataMixin, TestCase):
    """Fiche détaillée d'un cours : examens, inscriptions et moyenne."""

    def test_detail_affiche_infos_notes_et_moyenne(self):
        self._creer_etudiants(2)
        self._inscriptions(2)
        cours = Cours.objects.get(nom='Langage de programmation mobile')
        for ins in Inscription.objects.filter(examen__cours=cours):
            ins.note_interro = 3.5
            ins.note_tp = 4
            ins.note_examen = 6
            ins.save()
        reponse = self.client.get(reverse('cours_detail', args=[cours.pk]))
        self.assertEqual(reponse.status_code, 200)
        contenu = reponse.content.decode()
        self.assertIn('Langage de programmation mobile', contenu)
        self.assertIn('NOM0', contenu)
        self.assertIn('SESSION 1, SEMESTRE 1 2025 - 2026', contenu)
        # Moyenne du cours : 13,50 affichée
        self.assertIn('13,50', contenu)

    def test_detail_sans_examen(self):
        cours = Cours.objects.create(
            nom='Cours orphelin', promotion=self.promotion,
            enseignant=self.enseignant)
        reponse = self.client.get(reverse('cours_detail', args=[cours.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, 'Aucun examen programmé pour ce cours.')

    def test_detail_inexistant_renvoie_404(self):
        reponse = self.client.get(reverse('cours_detail', args=[9999]))
        self.assertEqual(reponse.status_code, 404)

    def test_liste_lien_vers_detail(self):
        cours = Cours.objects.get(nom='Langage de programmation mobile')
        reponse = self.client.get(reverse('cours_list'))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, reverse('cours_detail', args=[cours.pk]))


class ExcelImportTests(TestCase):
    """Tests pour les fonctionnalités d'importation Excel."""

    def setUp(self):
        self.user = User.objects.create_user('admin_import', 'import@example.com', 'pass12345')

    def _creer_excel_bytes(self, df_dict, sheet_name='Sheet1'):
        import pandas as pd
        bio = BytesIO()
        with pd.ExcelWriter(bio, engine='openpyxl') as writer:
            if isinstance(df_dict, dict):
                df = pd.DataFrame(df_dict)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
            elif isinstance(df_dict, pd.DataFrame):
                df_dict.to_excel(writer, sheet_name=sheet_name, index=False)
        bio.seek(0)
        return bio

    def test_import_grille_deliberation_avec_colonne_numerique_float(self):
        """Grille (n° d'ordre + nom, sans en-tête) : les numéros doivent être lus.

        Régression : dès qu'une cellule de la colonne 0 était vide, pandas la
        typait en `float` (« 1.0 ») et *toute* la grille était ignorée.
        """
        import pandas as pd
        from app.excel_import import import_etudiants_excel

        bio = BytesIO()
        with pd.ExcelWriter(bio, engine='openpyxl') as writer:
            pd.DataFrame([
                [1, 'KABISAYI TRESOR'],
                [None, 'Total général'],      # force le typage float
                [2, 'MUJINGA MAGUY'],
            ]).to_excel(writer, sheet_name='L3 INFO A', index=False,
                        header=False)
        bio.seek(0)

        res = import_etudiants_excel(bio)

        self.assertTrue(res['success'], res['errors'])
        self.assertEqual(res['created'], 2)
        self.assertEqual(
            list(Etudiant.objects.filter(promotion__nom='L3 INFO A')
                 .order_by('numero_etudiant')
                 .values_list('numero_etudiant', 'noms')),
            [('L3INFOA-001', 'KABISAYI TRESOR'),
             ('L3INFOA-002', 'MUJINGA MAGUY')])

        # Ré-import : aucune écriture (ni création, ni mise à jour).
        bio.seek(0)
        res = import_etudiants_excel(bio)
        self.assertEqual((res['created'], res['updated']), (0, 0))
        self.assertEqual(res['skipped'], 2)

    def test_import_etudiants_excel(self):
        from app.excel_import import import_etudiants_excel
        bio = self._creer_excel_bytes({
            'Nom': ['KABISAYI'],
            'Prénom': ['Trésor'],
            'N° Étudiant': ['L3INFO-001'],
            'Promotion': ['L3 INFO A']
        })
        res = import_etudiants_excel(bio)
        self.assertTrue(res['success'])
        self.assertEqual(res['created'], 1)
        self.assertTrue(Etudiant.objects.filter(numero_etudiant='L3INFO-001').exists())

    def test_import_enseignants_excel(self):
        from app.excel_import import import_enseignants_excel
        bio = self._creer_excel_bytes({
            'Nom': ['BABANEMI'],
            'Prénom': ['Jean'],
            'Email': ['jean.babanemi@example.com']
        })
        res = import_enseignants_excel(bio)
        self.assertTrue(res['success'])
        self.assertEqual(res['created'], 1)
        self.assertTrue(Enseignant.objects.filter(email='jean.babanemi@example.com').exists())

    def test_import_cours_excel(self):
        from app.excel_import import import_cours_excel
        bio = self._creer_excel_bytes({
            'Cours': ['Programmation Web'],
            'Promotion': ['L3 INFO A'],
            'Enseignant': ['BABANEMI Jean'],
            'Coefficient': [2]
        })
        res = import_cours_excel(bio)
        self.assertTrue(res['success'])
        self.assertEqual(res['created'], 1)
        self.assertTrue(Cours.objects.filter(nom='Programmation Web').exists())

    # --- Idempotence et optimisation des imports -------------------------

    def _excel_etudiants(self, noms_prenoms, promo='L2 INFO', prefixe='IMP'):
        return self._creer_excel_bytes({
            'Nom': [n for n, _ in noms_prenoms],
            'Prénom': [p for _, p in noms_prenoms],
            'N° Étudiant': [f'{prefixe}-{i:03d}'
                            for i in range(len(noms_prenoms))],
            'Promotion': [promo] * len(noms_prenoms),
        })

    def test_reimport_etudiants_identiques_est_ignore(self):
        """Ré-importer le même fichier ne doit générer aucune écriture."""
        from app.excel_import import import_etudiants_excel
        donnees = [('KABISAYI', 'Trésor'), ('MUKENDI', 'Alain')]

        premier = import_etudiants_excel(self._excel_etudiants(donnees))
        self.assertEqual(premier['created'], 2)

        second = import_etudiants_excel(self._excel_etudiants(donnees))
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['updated'], 0)
        self.assertEqual(second['skipped'], 2)
        self.assertEqual(Etudiant.objects.filter(noms__icontains='KABISAYI')
                         .count(), 1)

    def test_reimport_etudiant_modifie_le_nom(self):
        """Un nom corrigé dans le fichier doit être appliqué."""
        from app.excel_import import import_etudiants_excel
        import_etudiants_excel(self._excel_etudiants([('KABISAYI', 'Tresor')]))
        res = import_etudiants_excel(
            self._excel_etudiants([('KABISAYI', 'Trésor')]))
        self.assertEqual(res['created'], 0)
        self.assertEqual(res['updated'], 1)
        self.assertTrue(Etudiant.objects.filter(noms='KABISAYI Trésor')
                        .exists())

    def test_reimport_enseignants_ne_cree_pas_de_doublon(self):
        """La déduplication se fait sur les noms, pas sur l'email."""
        from app.excel_import import import_enseignants_excel
        bio = lambda: self._creer_excel_bytes({
            'Nom': ['BABANEMI'], 'Prénom': ['Albert'],
        })
        premier = import_enseignants_excel(bio())
        self.assertEqual(premier['created'], 1)
        second = import_enseignants_excel(bio())
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['skipped'], 1)
        self.assertEqual(
            Enseignant.objects.filter(noms__iexact='BABANEMI Albert').count(),
            1)

    def test_reimport_cours_ne_cree_pas_de_doublon(self):
        """Un cours (promotion, nom) déjà présent n'est pas recréé."""
        from app.excel_import import import_cours_excel
        bio = lambda: self._creer_excel_bytes({
            'Cours': ['Programmation Web'],
            'Promotion': ['L2 INFO'],
            'Enseignant': ['BABANEMI Albert'],
        })
        import_cours_excel(bio())
        res = import_cours_excel(bio())
        self.assertEqual(res['created'], 0)
        self.assertEqual(
            Cours.objects.filter(nom='Programmation Web').count(), 1)

    def test_meme_cours_dans_deux_promotions(self):
        """La contrainte (promotion, nom) ne doit pas gêner deux promotions."""
        from app.excel_import import import_cours_excel
        bio = self._creer_excel_bytes({
            'Cours': ['Programmation Web', 'Programmation Web'],
            'Promotion': ['L2 INFO', 'L3 SCF LMD'],
            'Enseignant': ['BABANEMI Albert', 'BABANEMI Albert'],
        })
        res = import_cours_excel(bio)
        self.assertEqual(res['created'], 2)
        self.assertEqual(
            Cours.objects.filter(nom='Programmation Web').count(), 2)

    def test_promotion_nom_unique(self):
        """Deux promotions de même nom sont interdites en base."""
        from django.db import IntegrityError
        Promotion.objects.create(nom='Promo Unique Test')
        with self.assertRaises(IntegrityError):
            Promotion.objects.create(nom='Promo Unique Test')

    def test_import_etudiant_sans_numero_genere_un_matricule(self):
        """Sans colonne matricule, un numéro ETU-######## est attribué."""
        from app.excel_import import import_etudiants_excel
        bio = self._creer_excel_bytes({
            'Nom': ['KALALA'], 'Prénom': ['Joseph'], 'Promotion': ['L2 INFO'],
        })
        res = import_etudiants_excel(bio)
        self.assertEqual(res['created'], 1)
        etudiant = Etudiant.objects.get(noms='KALALA Joseph')
        self.assertRegex(etudiant.numero_etudiant, r'^ETU-\d{8}$')

    def test_import_etudiants_sans_requete_par_etudiant(self):
        """Garde-fou anti N+1 : l'import ne coûte pas ~5 requêtes par ligne."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from app.excel_import import import_etudiants_excel

        nb = 50
        donnees = [(f'NOM{i}', f'Prenom{i}') for i in range(nb)]
        bio = self._excel_etudiants(donnees, promo='L2 SINFO',
                                    prefixe='PLUR')

        with CaptureQueriesContext(connection) as ctx:
            res = import_etudiants_excel(bio)

        self.assertEqual(res['created'], nb)
        # Référentiel (promotion, enseignants, cours) + étudiantes de la
        # promotion + 1 INSERT par étudiant : on reste sous 1,5 requête/ligne,
        # là où un import naïf en consommerait 4 à 5.
        self.assertLess(len(ctx), int(nb * 1.5))

    def test_reimport_etudiants_ne_modifie_rien(self):
        """Un 2e import du même fichier ne doit produire ni création ni UPDATE."""
        from app.excel_import import import_etudiants_excel
        donnees = {
            'Nom': ['KABISAYI'],
            'Prénom': ['Trésor'],
            'N° Étudiant': ['L3INFO-777'],
            'Promotion': ['L3 INFO A'],
        }
        premier = import_etudiants_excel(self._creer_excel_bytes(donnees))
        self.assertEqual((premier['created'], premier['updated']),
                         (1, 0))

        second = import_etudiants_excel(self._creer_excel_bytes(donnees))
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['updated'], 0)
        self.assertEqual(second['skipped'], 1)
        self.assertEqual(Etudiant.objects.count(), 1)

    def test_import_etudiant_conserve_le_matricule_fourni(self):
        """Le matricule du fichier n'est pas écrasé par un numéro généré."""
        from app.excel_import import import_etudiants_excel
        bio = self._creer_excel_bytes({
            'Nom': ['MUJINGA'],
            'Prénom': ['Kabongo'],
            'N° Étudiant': ['L3INFOB-042'],
            'Promotion': ['L3 INFO A'],
        })
        import_etudiants_excel(bio)
        etudiant = Etudiant.objects.get()
        self.assertEqual(etudiant.numero_etudiant, 'L3INFOB-042')
        self.assertEqual(etudiant.noms, 'MUJINGA Kabongo')

    def test_import_grille_etudiants_est_idempotent(self):
        """Commande `import_etudiants` : grille -> matricules -> 2e passage neutre."""
        import shutil
        import tempfile
        from pathlib import Path

        import pandas as pd
        from app.management.commands import import_etudiants as commande

        dossier = Path(tempfile.mkdtemp())
        # Grille de délibération : col 0 = n° d'ordre, col 1 = « NOMS PRÉNOM »
        grille = pd.DataFrame({
            0: [1, 2, 'Total'],
            1: ['KABISAYI TRESOR', 'MUJINGA KABONGO', ''],
        })
        grille.to_excel(
            dossier / 'L1 INFO LMD A_2025_2026.xlsx', header=False, index=False)

        ancien_dossier = commande.DATA_DIR
        commande.DATA_DIR = dossier
        try:
            call_command('import_etudiants')
            self.assertEqual(Etudiant.objects.count(), 2)
            self.assertEqual(
                set(Etudiant.objects.values_list('numero_etudiant', flat=True)),
                {'L1INFOA-001', 'L1INFOA-002'})

            # Deuxième exécution : aucun doublon, aucune modification.
            call_command('import_etudiants')
            self.assertEqual(Etudiant.objects.count(), 2)
            self.assertEqual(
                set(Etudiant.objects.values_list('noms', flat=True)),
                {'KABISAYI TRESOR', 'MUJINGA KABONGO'})
        finally:
            commande.DATA_DIR = ancien_dossier
            shutil.rmtree(dossier, ignore_errors=True)

    def test_vue_import_anonyme_redirige(self):
        url = reverse('import_excel')
        reponse = self.client.get(url)
        self.assertEqual(reponse.status_code, 302)

    def test_vue_import_connecte(self):
        self.client.force_login(self.user)
        url = reverse('import_excel')
        reponse = self.client.get(url)
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, 'Importation Excel')

    def test_vue_import_post_etudiants(self):
        self.client.force_login(self.user)
        bio = self._creer_excel_bytes({
            'Nom': ['MUKENDI'],
            'Prénom': ['Alain'],
            'N° Étudiant': ['L2INFO-002'],
            'Promotion': ['L2 INFO']
        })
        bio.name = 'etudiants_test.xlsx'
        url = reverse('import_excel')
        reponse = self.client.post(url, {
            'type_import': 'etudiants',
            'fichier_excel': bio
        })
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, 'Import étudiants terminé')
        self.assertTrue(Etudiant.objects.filter(numero_etudiant='L2INFO-002').exists())


class PromotionBulkDeleteTests(TestCase):
    """Tests pour la suppression en groupe de promotions."""

    def setUp(self):
        self.user = User.objects.create_user('admin_bulk', 'bulk@example.com', 'pass12345')
        self.p1 = Promotion.objects.create(nom='Promotion Test 1')
        self.p2 = Promotion.objects.create(nom='Promotion Test 2')
        self.p3 = Promotion.objects.create(nom='Promotion Test 3')

    def test_bulk_delete_anonyme_redirige(self):
        url = reverse('promotion_bulk_delete')
        response = self.client.post(url, {'promotion_ids': [self.p1.pk, self.p2.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_bulk_delete_connecte_succes(self):
        self.client.force_login(self.user)
        url = reverse('promotion_bulk_delete')
        response = self.client.post(url, {'promotion_ids': [str(self.p1.pk), str(self.p2.pk)]})
        self.assertRedirects(response, reverse('promotion_list'))
        self.assertFalse(Promotion.objects.filter(pk=self.p1.pk).exists())
        self.assertFalse(Promotion.objects.filter(pk=self.p2.pk).exists())
        self.assertTrue(Promotion.objects.filter(pk=self.p3.pk).exists())

    def test_bulk_delete_aucun_selectionne(self):
        self.client.force_login(self.user)
        url = reverse('promotion_bulk_delete')
        response = self.client.post(url, {'promotion_ids': []})
        self.assertRedirects(response, reverse('promotion_list'))
        self.assertEqual(Promotion.objects.count(), 3)


class CoursBulkDeleteTests(TestCase):
    """Tests pour la suppression en groupe de cours."""

    def setUp(self):
        self.user = User.objects.create_user('admin_cours_bulk', 'coursbulk@example.com', 'pass12345')
        self.promo = Promotion.objects.create(nom='Promotion Cours Test')
        self.c1 = Cours.objects.create(nom='Cours Test 1', promotion=self.promo)
        self.c2 = Cours.objects.create(nom='Cours Test 2', promotion=self.promo)
        self.c3 = Cours.objects.create(nom='Cours Test 3', promotion=self.promo)

    def test_bulk_delete_anonyme_redirige(self):
        url = reverse('cours_bulk_delete')
        response = self.client.post(url, {'cours_ids': [self.c1.pk, self.c2.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_bulk_delete_connecte_succes(self):
        self.client.force_login(self.user)
        url = reverse('cours_bulk_delete')
        response = self.client.post(url, {'cours_ids': [str(self.c1.pk), str(self.c2.pk)]})
        self.assertRedirects(response, reverse('cours_list'))
        self.assertFalse(Cours.objects.filter(pk=self.c1.pk).exists())
        self.assertFalse(Cours.objects.filter(pk=self.c2.pk).exists())
        self.assertTrue(Cours.objects.filter(pk=self.c3.pk).exists())

    def test_bulk_delete_aucun_selectionne(self):
        self.client.force_login(self.user)
        url = reverse('cours_bulk_delete')
        response = self.client.post(url, {'cours_ids': []})
        self.assertRedirects(response, reverse('cours_list'))
        self.assertEqual(Cours.objects.filter(promotion=self.promo).count(), 3)


class AlignerCoursGrilleCommandTest(TestCase):
    """Commande `aligner_cours_grille` : intitulés + crédits depuis la grille."""

    @staticmethod
    def _ecrire_grille(dossier, ues):
        """Écrit une grille de délibération minimale (lignes 7 et 8).

        `ues` : liste de tuples (intitulé, crédits). Un marqueur « FIN » est
        ajouté en fin de ligne d'en-tête, comme dans le fichier réel : il ne
        doit pas être pris pour une UE.
        """
        import pandas as pd
        largeur = 4 + len(ues)
        lignes = [
            ['ESFORCA/INPP'],
            ['2EME SEMESTRE'],
            ['GRILLE DE DELIBERATION 2025 - 2026'],
            [],
            ['', '', 'PREMIER SEMESTRE'] + [''] * (largeur - 3),
            ['', 'GROUPE'] + [''] * (largeur - 2),
            ['', "UNITES D'ENSEIGNEMENT"] + [n for n, _ in ues] + ['FIN'],
            ['', 'Crédits'] + [c for _, c in ues] + [''],
            ['', 'N°', 'NOMS'] + [''] * (largeur - 3),
        ]
        # Toutes les lignes doivent avoir la même largeur pour pandas.
        lignes = [ligne + [''] * (largeur - len(ligne) + 1) for ligne in lignes]
        chemin = dossier / 'L1 INFO LMD A_2025_2026.xlsx'
        pd.DataFrame(lignes).to_excel(chemin, header=False, index=False)
        return chemin

    def _contexte(self, ues, cours_existants):
        """Prépare une promo L1 INFO A + sa grille et patche DATA_DIR.

        Retourne la promotion créée.
        """
        import shutil
        import tempfile
        from pathlib import Path

        from app.management.commands import aligner_cours_grille as commande

        dossier = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dossier, ignore_errors=True)
        self._ecrire_grille(dossier, ues)

        promotion = Promotion.objects.create(nom='L1 INFO A')
        for nom, coefficient in cours_existants:
            Cours.objects.create(nom=nom, coefficient=coefficient,
                                 promotion=promotion)

        dossier_initial = commande.DATA_DIR
        self.addCleanup(setattr, commande, 'DATA_DIR', dossier_initial)
        commande.DATA_DIR = dossier
        return promotion

    def test_renomme_et_applique_les_credits(self):
        """Les cours sont renommés et leur coefficient prend le crédit."""
        self._contexte(
            ues=[('Informatique Générale', 4), ('Bureautique', 4),
                 ("Stage d'observation", 2)],
            cours_existants=[('INFO GENERALE', 1), ('LABORATOIRE', 1)])

        call_command('aligner_cours_grille')

        self.assertEqual(
            sorted(Cours.objects.values_list('nom', 'coefficient')),
            sorted([('Informatique Générale', 4), ('Bureautique', 4),
                    ("Stage d'observation", 2)]))

    def test_dry_run_ne_modifie_pas_la_base(self):
        """--dry-run affiche sans écrire : intitulés et crédits intacts."""
        self._contexte(
            ues=[('Informatique Générale', 4)],
            cours_existants=[('INFO GENERALE', 1)])

        call_command('aligner_cours_grille', dry_run=True)

        cours = Cours.objects.get()
        self.assertEqual((cours.nom, cours.coefficient), ('INFO GENERALE', 1))
        self.assertEqual(Cours.objects.count(), 1)

    def test_commande_est_idempotente(self):
        """Un second passage ne crée aucun doublon ni modification."""
        self._contexte(
            ues=[('Informatique Générale', 4), ('Stage d\'observation', 2)],
            cours_existants=[('INFO GENERALE', 1)])

        call_command('aligner_cours_grille')
        call_command('aligner_cours_grille')

        self.assertEqual(Cours.objects.count(), 2)
        self.assertEqual(
            sorted(Cours.objects.values_list('nom', flat=True)),
            ['Informatique Générale', "Stage d'observation"])

    def test_promotion_absente_leve_une_erreur(self):
        """Sans la promotion cible, la commande échoue explicitement."""
        import shutil
        import tempfile
        from pathlib import Path

        from django.core.management.base import CommandError

        from app.management.commands import aligner_cours_grille as commande

        dossier = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dossier, ignore_errors=True)
        self._ecrire_grille(dossier, [('Informatique Générale', 4)])
        dossier_initial = commande.DATA_DIR
        self.addCleanup(setattr, commande, 'DATA_DIR', dossier_initial)
        commande.DATA_DIR = dossier

        with self.assertRaises(CommandError):
            call_command('aligner_cours_grille')


class GrilleModelesTests(TestCase):
    """Modèles de grille de délibération : structure, contraintes, barème."""

    @classmethod
    def setUpTestData(cls):
        cls.promotion = Promotion.objects.create(nom='L1 INFO A')
        cls.session = Session.objects.create(
            nom='Rattrapage SEMESTRE 2 2025 - 2026', semestre=2,
            type_session='rattrapage',
            date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 30))
        cls.cours = Cours.objects.create(
            nom='Informatique Générale', coefficient=4,
            promotion=cls.promotion)
        cls.etudiant = Etudiant.objects.create(
            noms='KATANGA TSHIKUNGA FISTON', numero_etudiant='L1INFOA-001',
            promotion=cls.promotion)

    def _grille(self, **kwargs):
        defauts = {
            'promotion': self.promotion,
            'session': self.session,
            'annee_academique': '2025-2026',
            'etablissement': 'ESFORCA/INPP',
            'total_credits': 76,
            'fichier_source': 'L1 INFO LMD A_2025_2026.xlsx',
        }
        defauts.update(kwargs)
        return Grille.objects.create(**defauts)

    def _ue(self, grille, **kwargs):
        defauts = {
            'grille': grille, 'cours': self.cours,
            'intitule': 'Informatique Générale', 'credits': 4,
            'semestre': 1, 'groupe': 'IBA', 'ordre': 1,
        }
        defauts.update(kwargs)
        return GrilleUE.objects.create(**defauts)

    def _ligne(self, grille, **kwargs):
        defauts = {
            'grille': grille, 'etudiant': self.etudiant, 'rang': 1,
            'credits_s1': 16, 'credits_s2': 17, 'credits_total': 33,
            'nb_ue_reprendre': 15, 'total_pondere': 566,
            'moyenne': '7.45', 'pourcentage': '37.24', 'decision': 'NV',
        }
        defauts.update(kwargs)
        return GrilleEtudiant.objects.create(**defauts)

    def test_bareme_est_le_total_pondere_maximal(self):
        """Le dénominateur du fichier vaut « crédits × 20 » (1520 ici)."""
        self.assertEqual(self._grille().bareme, 76 * 20)

    def test_ue_est_validee_a_partir_de_dix(self):
        """Seuil de validation du fichier : note >= 10 (case vide = échec)."""
        ligne = self._ligne(self._grille())
        ue = self._ue(ligne.grille)

        validee = GrilleNote.objects.create(ligne=ligne, ue=ue, note='10')
        juste_echouee = GrilleNote.objects.create(
            ligne=ligne, ue=self._ue(ligne.grille, intitule='Bureautique',
                                     ordre=2), note='9.99')
        vide = GrilleNote.objects.create(
            ligne=ligne, ue=self._ue(ligne.grille, intitule='Algorithmique 1',
                                     ordre=3), note=None)

        self.assertTrue(validee.est_validee)
        self.assertFalse(juste_echouee.est_validee)
        self.assertFalse(vide.est_validee)

    def test_une_seule_grille_par_promotion_session_annee(self):
        """Rejouer le même import ne doit pas dupliquer la grille."""
        self._grille()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._grille()

    def test_ue_non_dupliquee_dans_une_grille(self):
        grille = self._grille()
        self._ue(grille)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._ue(grille, ordre=2)

    def test_rang_unique_dans_une_grille(self):
        grille = self._grille()
        self._ligne(grille)
        autre = Etudiant.objects.create(
            noms='MABANGI WAMABANGI', numero_etudiant='L1INFOA-002',
            promotion=self.promotion)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._ligne(grille, etudiant=autre)

    def test_une_seule_note_par_etudiant_et_ue(self):
        grille = self._grille()
        ligne = self._ligne(grille)
        ue = self._ue(grille)
        GrilleNote.objects.create(ligne=ligne, ue=ue, note='12')
        with self.assertRaises(IntegrityError), transaction.atomic():
            GrilleNote.objects.create(ligne=ligne, ue=ue, note='14')

    def test_note_hors_bornes_rejetee(self):
        """La note est bornée 0-20, comme dans la grille."""
        from django.core.exceptions import ValidationError
        ligne = self._ligne(self._grille())
        note = GrilleNote(ligne=ligne, ue=self._ue(ligne.grille), note='25')
        with self.assertRaises(ValidationError):
            note.full_clean()

    def test_supprimer_un_cours_detache_l_ue_de_la_grille(self):
        """La grille reste lisible si le cours est supprimé du référentiel."""
        grille = self._grille()
        ue = self._ue(grille)
        self.cours.delete()
        ue.refresh_from_db()
        self.assertIsNone(ue.cours)
        self.assertEqual(ue.intitule, 'Informatique Générale')

