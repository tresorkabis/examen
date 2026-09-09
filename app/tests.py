"""Suite de tests de l'application « app ».

Couvre : les calculs de moyenne (modèle Inscription), la fiche de cote,
l'inscription/désinscription des étudiants, la pagination hors bornes et
l'authentification (accès en écriture protégé).
"""
from datetime import date, datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Promotion, Enseignant, Etudiant, Cours, Session,
                     Examen, Inscription)


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
        # 30 examens -> 2 pages ; le filtre doit être conservé dans ?page=2
        for i in range(30):
            Examen.objects.create(
                cours=self.cours1, session=self.sess1,
                date_examen=timezone.make_aware(datetime(2026, 3, 1, 9, 0)))
        response = self._liste({'q': 'mobile'})
        contenu = response.content.decode()
        self.assertIn('?page=2&amp;q=mobile', contenu)
