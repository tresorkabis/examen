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
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Promotion, Enseignant, Etudiant, Cours, Session,
                     Examen, Inscription)
from .forms import ExamenForm


class BaseDataMixin:
    """Crée les fixtures communes à tous les tests."""

    @classmethod
    def setUpTestData(cls):
        cls.promotion = Promotion.objects.create(nom='L3 INFO A')
        cls.enseignant = Enseignant.objects.create(
            nom='KABISAYI', prenom='TRESOR',
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
                nom=f'NOM{i}', prenom=f'Prenom{i}',
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
            nom='DUPONT', prenom='Jean',
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
                nom=f'NOM{i}', prenom='P',
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
            nom='KABISAYI', prenom='TRESOR',
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
            nom='DUPONT', prenom='Jean',
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
            nom='KABISAYI', prenom='TRESOR',
            email='filter.ens1@example.com')
        cls.ens2 = Enseignant.objects.create(
            nom='MUJINGA', prenom='MAGUY',
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
        self.assertIn('Langage de programmation mobile (KABISAYI)', contenu)
        self.assertIn('Ethique &amp; Deontologie (MUJINGA)', contenu)

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
            nom='KARIM', prenom='Ali', email='a.karim@example.com')
        cls.ens2 = Enseignant.objects.create(
            nom='LAMBER', prenom='Benoit', email='b.lamber@example.com')
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
            nom='NDAYE', prenom='Jean',
            email='j.ndaye@example.com',
            numero_etudiant='L1-001', promotion=cls.promo1)
        cls.etu2 = Etudiant.objects.create(
            nom='KAMANDA', prenom='Marie',
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
            nom='ENSEIGNANT', prenom='TEST',
            email='enseignant.merge@example.com')
        self.promo_a = Promotion.objects.create(nom='L2 SD A')
        self.promo_b = Promotion.objects.create(nom='L2 TS A')

    def _etudiant(self, promo, i):
        return Etudiant.objects.create(
            nom=f'ETU{i}', prenom=f'Prenom{i}',
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
            nom='KABISAYI', prenom='TRESOR',
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

    def _etudiant(self, nom, prenom, numero):
        return Etudiant.objects.create(
            nom=nom, prenom=prenom,
            email=f'{numero.lower()}@example.com',
            numero_etudiant=numero, promotion=self.ex1.cours.promotion)

    def test_liste_examens_et_participants(self):
        etu1 = self._etudiant('ASSANI', 'LAZARINE', 'L1INFOA-001')
        etu2 = self._etudiant('MABANGI', 'WAMABANGI', 'L1INFOA-002')
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

        etu1 = self._etudiant('ASSANI', 'LAZARINE', 'L1INFOA-001')
        etu2 = self._etudiant('MABANGI', 'WAMABANGI', 'L1INFOA-002')
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
            nom='SANS', prenom='COURS', email='sans.cours@example.com')
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
