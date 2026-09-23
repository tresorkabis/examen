from collections import OrderedDict
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.db.models import Count, F, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.text import slugify
from django.views.generic import (ListView, CreateView, UpdateView, DeleteView,
                                  DetailView)

from .forms import (PromotionForm, EnseignantForm, EtudiantForm, CoursForm,
                    SessionForm, ExamenForm, ExcelImportForm)
from .models import (Etudiant, Cours, Session, Enseignant, Promotion, Examen,
                     Inscription, Grille, GrilleUE, GrilleEtudiant, GrilleNote,
                     HistoriquePromotion)
from .excel_import import (import_etudiants_excel, import_enseignants_excel,
                          import_cours_excel, import_grille_excel)


def _calcul_compteurs(session):
    """Compteurs du tableau de bord, liés à la session en cours.

    Si une session active (est_active) existe, les compteurs (étudiants,
    cours, enseignants, promotions, examens) ne concernent que cette session ;
    sinon ils représentent les totaux globaux de la base de données.
    """
    if session is None:
        return {
            'total_etudiants': Etudiant.objects.count(),
            'total_cours': Cours.objects.count(),
            'total_sessions': Session.objects.count(),
            'total_enseignants': Enseignant.objects.count(),
            'total_promotions': Promotion.objects.count(),
            'total_examens': Examen.objects.count(),
        }
    return {
        'total_etudiants': (Etudiant.objects
                            .filter(inscriptions__examen__session=session)
                            .distinct().count()),
        'total_cours': (Cours.objects
                        .filter(examens__session=session).distinct().count()),
        'total_sessions': 1,
        'total_enseignants': (Enseignant.objects
                              .filter(cours__examens__session=session)
                              .distinct().count()),
        'total_promotions': (Promotion.objects
                             .filter(cours__examens__session=session)
                             .distinct().count()),
        'total_examens': Examen.objects.filter(session=session).count(),
    }


def _compteurs_tableau_de_bord(session):
    """Compteurs mis en cache 60 s (une entrée par session ou « global »).

    La clé unique « dashboard_compteurs » contient un dictionnaire
    {clé de session : compteurs} ; les signaux la suppriment en entier
    à chaque écriture sur les modèles concernés.
    """
    cle_session = session.pk if session else 'global'
    par_cle = cache.get('dashboard_compteurs')
    if par_cle is None:
        par_cle = {}
    if cle_session not in par_cle:
        par_cle[cle_session] = _calcul_compteurs(session)
        cache.set('dashboard_compteurs', par_cle, 60)
    return par_cle[cle_session]


def dashboard(request):
    """Tableau de bord : statistiques liées à la session en cours.

    La session active (est_active) restreint les compteurs ; « Prochains
    examens » reste global (planification) et « Sessions récentes » montre
    les dernières sessions, la session en cours y étant signalée.
    """
    session_active = Session.objects.filter(est_active=True).first()
    compteurs = _compteurs_tableau_de_bord(session_active)
    prochains = (Examen.objects
                 .select_related('cours__promotion', 'cours__enseignant', 'session')
                 .filter(date_examen__gte=timezone.now())
                 .order_by('date_examen')[:5])
    dernieres_sessions = (Session.objects.order_by('-date_debut')[:3])
    context = {
        **compteurs,
        'prochains_examens': prochains,
        'dernieres_sessions': dernieres_sessions,
        'session_active': session_active,
    }
    return render(request, 'app/dashboard.html', context)


class SafePaginationMixin:
    """Si « ?page=N » est hors bornes, redirige vers la dernière page valide.

    Django lève normalement une 404 pour une page inexistante (ex. « ?page=8 »
    alors qu'il n'y a que 5 pages — lien périmé, favori, ancienne pagination).
    Ce mixin transforme cette 404 en redirection vers la dernière page,
    en conservant les éventuels autres paramètres GET de la requête.
    """

    def get(self, request, *args, **kwargs):
        try:
            return super().get(request, *args, **kwargs)
        except Http404:
            page_arg = request.GET.get(self.page_kwarg)
            queryset = self.get_queryset()
            page_size = self.get_paginate_by(queryset)
            # Ce n'est pas une 404 de pagination : on la propage telle quelle.
            if page_arg is None or not page_size:
                raise
            paginator = self.get_paginator(queryset, page_size)
            if paginator.num_pages < 1:
                raise
            try:
                requested = int(page_arg)
            except ValueError:
                requested = 1
            target = min(max(requested, 1), paginator.num_pages)
            query = request.GET.copy()
            query[self.page_kwarg] = target
            return HttpResponseRedirect(f"{request.path}?{query.urlencode()}")


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Conserve les paramètres GET (filtres…) dans les liens de pagination
        params = self.request.GET.copy()
        params.pop(self.page_kwarg, None)
        context['qs'] = f"&{params.urlencode()}" if params else ""
        return context


class DeleteMessageMixin:
    """Affiche un message de confirmation après une suppression.

    En Django 6, DeleteView passe par FormMixin : le POST appelle form_valid()
    (et non delete()), c'est donc là qu'il faut accrocher le message.
    """
    success_message = None

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.success_message:
            messages.success(self.request, self.success_message % {"object": self.object})
        return response


# --- CRUD ETUDIANT ---
class EtudiantListView(ListView):
    """Étudiants : cartes des promotions, puis liste d'une promotion au clic.

    Sans paramètre : grille de cartes (une par promotion non vide, avec
    effectifs annotés — sans charger aucun étudiant). Avec
    « ?promotion=<pk> » : liste paginée (50/page) des étudiants de cette
    promotion. Une recherche « ?q= » filtre les étudiants par nom /
    numéro / email et n'affiche que les promotions concernées.
    """
    model = Promotion
    template_name = "app/etudiant_list.html"
    context_object_name = "promotions"
    ETUDIANTS_PAR_PAGE = 50

    def get_queryset(self):
        q = self.request.GET.get('q', '').strip()
        promo_pk = self.request.GET.get('promotion', '').strip()
        if q:
            # Recherche en base (indexée) : ne charge que les promotions
            # ayant au moins un étudiant correspondant. Si une promotion est
            # sélectionnée, on restreint la recherche à celle-ci.
            filtres = (Q(etudiants__noms__icontains=q)
                       | Q(etudiants__numero_etudiant__icontains=q)
                       | Q(etudiants__email__icontains=q))
            qs = Promotion.objects.filter(filtres)
            if promo_pk.isdigit():
                qs = qs.filter(pk=int(promo_pk))
            return (qs
                    .distinct()
                    .prefetch_related(Prefetch(
                        'etudiants',
                        queryset=Etudiant.objects.filter(
                            Q(noms__icontains=q)
                            | Q(numero_etudiant__icontains=q)
                            | Q(email__icontains=q))
                        .order_by('noms')))
                    .annotate(nb_etudiants_total=Count('etudiants', distinct=True),
                              nb_cours=Count('cours', distinct=True))
                    .order_by('nom'))
        if self.request.GET.get('toutes') == '1':
            # Cartes seules : compteurs annotés, aucun étudiant chargé.
            return (Promotion.objects
                    .annotate(nb_etudiants_total=Count('etudiants', distinct=True),
                              nb_cours=Count('cours', distinct=True))
                    .order_by('nom'))
        # Par défaut : seules les promotions non vides (EXISTS, sans tout charger).
        return (Promotion.objects
                .filter(etudiants__isnull=False)
                .distinct()
                .annotate(nb_etudiants_total=Count('etudiants', distinct=True),
                          nb_cours=Count('cours', distinct=True))
                .order_by('nom'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        q = self.request.GET.get('q', '').strip()
        promo_pk = self.request.GET.get('promotion', '').strip()
        groups = list(context['promotions'])
        if q:
            for promotion in groups:
                # Déjà filtré/trié en base via le Prefetch du get_queryset.
                promotion.filtered_etudiants = list(promotion.etudiants.all())
                # Nombre de résultats dans cette carte lors d'une recherche.
                promotion.nb_match = len(promotion.filtered_etudiants)
        else:
            for promotion in groups:
                promotion.filtered_etudiants = []
                promotion.nb_match = 0
        context['groups'] = groups
        context['q'] = q
        context['toutes'] = self.request.GET.get('toutes') == '1'
        context['nb_etudiants_trouves'] = sum(p.nb_match for p in groups) if q else 0
        context['promotion_param'] = promo_pk
        # Promotion sélectionnée au clic : liste paginée (50/page),
        # sans recharger les autres promotions.
        promotion_active = None
        page_obj = None
        if promo_pk.isdigit():
            promo_id = int(promo_pk)
            promotion_active = next(
                (p for p in groups if p.pk == promo_id), None)
            if promotion_active is None:
                promotion_active = (
                    Promotion.objects
                    .filter(pk=promo_id)
                    .annotate(nb_etudiants_total=Count('etudiants', distinct=True),
                              nb_cours=Count('cours', distinct=True))
                    .first())
            if promotion_active is not None:
                qs_etudiants = Etudiant.objects.filter(promotion_id=promo_id)
                if q:
                    qs_etudiants = qs_etudiants.filter(
                        Q(noms__icontains=q)
                        | Q(numero_etudiant__icontains=q)
                        | Q(email__icontains=q))
                qs_etudiants = (qs_etudiants
                                .select_related('promotion')
                                .order_by('noms'))
                paginator = Paginator(qs_etudiants, self.ETUDIANTS_PAR_PAGE)
                page_obj = self._page_obj(paginator)
                promotion_active.filtered_etudiants = list(page_obj.object_list)
                promotion_active.nb_match = paginator.count if q else 0
                if q:
                    context['nb_etudiants_trouves'] = paginator.count
        context['promotion_active'] = promotion_active
        context['page_obj'] = page_obj
        # Conserve les paramètres GET (filtres…) dans les liens de pagination.
        params = self.request.GET.copy()
        params.pop('page', None)
        context['qs'] = f"&{params.urlencode()}" if params else ""
        return context

    def _page_obj(self, paginator):
        """Page demandée, rabattue sur la dernière page valide si hors bornes."""
        page_arg = self.request.GET.get('page')
        try:
            return paginator.page(page_arg or 1)
        except (PageNotAnInteger, EmptyPage):
            return paginator.page(paginator.num_pages or 1)


class EtudiantCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Etudiant
    form_class = EtudiantForm
    template_name = "app/etudiant_form.html"
    success_url = reverse_lazy("etudiant_list")
    success_message = "L'étudiant « %(noms)s » a été créé avec succès."

    def get_initial(self):
        """Pré-sélectionne la promotion via « ?promotion=<pk> » (bouton + d'une carte)."""
        initial = super().get_initial()
        promo = self.request.GET.get('promotion')
        if promo and promo.isdigit() and Promotion.objects.filter(pk=promo).exists():
            initial['promotion'] = int(promo)
        return initial


class EtudiantUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Etudiant
    form_class = EtudiantForm
    template_name = "app/etudiant_form.html"
    success_url = reverse_lazy("etudiant_list")
    success_message = "L'étudiant « %(noms)s » a été modifié."


class EtudiantDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Etudiant
    template_name = "app/etudiant_confirm_delete.html"
    success_url = reverse_lazy("etudiant_list")
    success_message = "L'étudiant « %(object)s » a été supprimé."


def _ues_a_reprendre(grille_lignes):
    """UE à reprendre dans toutes les grilles de l'étudiant.

    Une UE est « à reprendre » quand elle n'est pas acquise : note sous le
    seuil de validation (< 10, `GrilleNote.est_validee`) **ou** case vide
    (UE non notée). C'est la règle du fichier lui-même, dont la colonne
    « Nbre UE à reprendre » vaut `toutes les UE − UE acquises` : les
    matières non évaluées y sont déjà comptées comme à repasser.

    `grille_lignes` porte toutes les grilles fréquentées par l'étudiant —
    promotion courante comme années antérieures — l'agrégation couvre donc
    toutes ses promotions. Chaque UE d'une grille porte une ligne de note,
    même vide (créée à l'import) : c'est par elle que les UE non notées
    sont retrouvées.

    Retourne une liste triée de la grille la plus récente à la plus ancienne
    : {'grille', 'ligne', 'ues' (notes échouées ou non notées), 'credits'}.
    """
    notes_par_ligne = {}
    for note in (GrilleNote.objects
                 .filter(ligne__in=[ligne.pk for ligne in grille_lignes])
                 .select_related('ue', 'ligne__grille__promotion')):
        if note.est_validee:
            continue
        notes_par_ligne.setdefault(note.ligne_id, []).append(note)

    resultat = []
    for ligne in grille_lignes:      # déjà trié : plus récent d'abord
        echouees = notes_par_ligne.get(ligne.pk, [])
        if not echouees:
            continue
        echouees.sort(key=lambda n: (n.ue.semestre or 0, n.ue.ordre or 0,
                                     n.ue.intitule))
        resultat.append({
            'grille': ligne.grille,
            'ligne': ligne,
            'ues': echouees,
            'credits': sum(n.ue.credits for n in echouees),
        })
    return resultat


class EtudiantDetailView(DetailView):
    """Fiche détaillée d'un étudiant : inscriptions, notes et examens."""

    model = Etudiant
    template_name = "app/etudiant_detail.html"
    context_object_name = "etudiant"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        etudiant = self.object
        inscriptions = (etudiant.inscriptions
                        .select_related(
                            'examen__cours__promotion',
                            'examen__cours__enseignant',
                            'examen__session')
                        .order_by('examen__date_examen'))
        notes = [i.note for i in inscriptions if i.note is not None]
        moyenne = (sum(notes) / len(notes)) if notes else None

        # --- Relevé de grille de délibération (si la promotion possède une
        # grille importée) : une ligne par UE avec la note /20, crédits,
        # semestre et statut de validité, puis la synthèse délibérée.
        grille_lignes = list(
            GrilleEtudiant.objects
            .select_related('grille', 'grille__promotion', 'grille__session')
            .filter(etudiant=etudiant)
            .order_by('-grille__annee_academique', '-grille__id')
        )

        selected_grille_id = self.request.GET.get('grille')
        grille_ligne = None
        if selected_grille_id and selected_grille_id.isdigit():
            grille_ligne = next(
                (l for l in grille_lignes if l.grille_id == int(selected_grille_id)),
                None
            )
        if grille_ligne is None and grille_lignes:
            grille_ligne = grille_lignes[0]

        grille_ues_notes = []
        s1_notes = []
        s2_notes = []
        s1_credits_total = 0
        s2_credits_total = 0
        s1_credits_valides = 0
        s2_credits_valides = 0
        nb_ues_validees = 0
        nb_ues_echouees = 0
        nb_ues_non_notees = 0

        if grille_ligne is not None:
            ues = list(grille_ligne.grille.ues.order_by(
                'semestre', 'ordre', 'intitule'))
            notes_map = {
                n.ue_id: n for n in
                GrilleNote.objects.filter(ligne=grille_ligne)
                .select_related('ue')
            }
            for ue in ues:
                note_obj = notes_map.get(ue.pk)
                item = {'ue': ue, 'note': note_obj}
                grille_ues_notes.append(item)
                if ue.semestre == 1:
                    s1_notes.append(item)
                    s1_credits_total += ue.credits
                    if note_obj and note_obj.est_validee:
                        s1_credits_valides += ue.credits
                else:
                    s2_notes.append(item)
                    s2_credits_total += ue.credits
                    if note_obj and note_obj.est_validee:
                        s2_credits_valides += ue.credits

                if note_obj and note_obj.est_validee:
                    nb_ues_validees += 1
                elif note_obj and note_obj.note is not None:
                    nb_ues_echouees += 1
                else:
                    nb_ues_non_notees += 1

        # --- Parcours de l'étudiant : une étape par année fréquentée. C'est
        # ici que les étudiants de L2 et de L3 retrouvent l'historique de
        # leurs années antérieures (promotion, année, crédits, décision et
        # moyenne délibérées), renseigné à l'import de chaque grille.
        lignes_par_grille = {ligne.grille_id: ligne for ligne in grille_lignes}
        parcours = [
            {
                'etape': etape,
                'ligne': lignes_par_grille.get(etape.grille_id),
                'est_courante': (etape.promotion_id == etudiant.promotion_id
                                 and etape.date_fin is None),
            }
            for etape in (HistoriquePromotion.objects
                          .filter(etudiant=etudiant)
                          .select_related('promotion', 'grille'))
        ]

        # --- UE à reprendre : consolidé sur TOUTES les grilles de
        # l'étudiant (promotion courante et années antérieures) — c'est ce
        # que la délibération exige de repasser pour obtenir son année.
        ues_a_reprendre = _ues_a_reprendre(grille_lignes)

        ctx.update({
            'inscriptions': inscriptions,
            'nb_examens': len(inscriptions),
            'moyenne_generale': moyenne,
            'parcours': parcours,
            'grille_ligne': grille_ligne,
            'grille_lignes': grille_lignes,
            'grille_ues_notes': grille_ues_notes,
            's1_notes': s1_notes,
            's2_notes': s2_notes,
            's1_credits_total': s1_credits_total,
            's2_credits_total': s2_credits_total,
            's1_credits_valides': s1_credits_valides,
            's2_credits_valides': s2_credits_valides,
            'nb_ues_validees': nb_ues_validees,
            'nb_ues_echouees': nb_ues_echouees,
            'nb_ues_non_notees': nb_ues_non_notees,
            'ues_a_reprendre': ues_a_reprendre,
            'nb_ues_a_reprendre': sum(len(e['ues']) for e in ues_a_reprendre),
            'nb_credits_a_reprendre': sum(e['credits'] for e in ues_a_reprendre),
        })
        return ctx


# --- CRUD PROMOTION ---
class PromotionListView(SafePaginationMixin, ListView):
    model = Promotion
    template_name = "app/promotion_list.html"
    context_object_name = "promotions"
    paginate_by = 25

    def get_queryset(self):
        return (Promotion.objects
                .annotate(
                    nb_etudiants=Count('etudiants', distinct=True),
                    nb_cours=Count('cours', distinct=True),
                    nb_enseignants=Count('cours__enseignant', distinct=True),
                )
                .order_by('nom'))


class PromotionDetailView(DetailView):
    """Fiche détaillée d'une promotion : effectif, cours et enseignants."""
    model = Promotion
    template_name = "app/promotion_detail.html"
    context_object_name = "promotion"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        promotion = self.object
        cours = (promotion.cours
                 .select_related('enseignant')
                 .order_by('nom'))
        etudiants = list(promotion.etudiants.order_by('noms'))

        # --- Antécédents : étapes de parcours antérieures à cette promotion.
        # Les promotions de L2 et de L3 affichent ainsi, pour chaque étudiant,
        # les années déjà suivies (promotion, année, décision, moyenne) —
        # l'étape est renseignée automatiquement à l'import de chaque grille.
        etapes = list(HistoriquePromotion.objects
                      .filter(etudiant__promotion=promotion)
                      .exclude(promotion=promotion)
                      .select_related('promotion', 'grille')
                      .order_by('etudiant_id', '-annee_academique'))
        ids_grilles = {etape.grille_id for etape in etapes if etape.grille_id}
        lignes = {
            (ligne.grille_id, ligne.etudiant_id): ligne
            for ligne in GrilleEtudiant.objects.filter(
                etudiant__promotion=promotion, grille_id__in=ids_grilles)
        }
        antecedents = {}
        for etape in etapes:
            antecedents.setdefault(etape.etudiant_id, []).append({
                'etape': etape,
                'ligne': lignes.get((etape.grille_id, etape.etudiant_id)),
            })
        for etudiant in etudiants:
            etudiant.antecedents = antecedents.get(etudiant.pk, [])
        nb_avec_antecedents = sum(1 for e in etudiants if e.antecedents)

        # Enseignants intervenant dans cette promotion : requête ORM triée,
        # avec le nombre de cours dispensés par chacun.
        enseignants = (Enseignant.objects
                       .filter(cours__promotion=promotion)
                       .annotate(nb_cours=Count('cours',
                                                filter=Q(cours__promotion=promotion),
                                                distinct=True))
                       .order_by('noms'))
        nb_noted = Inscription.objects.filter(
            etudiant__promotion=promotion).count()

        # --- Grilles de délibération de la promotion : une par année. C'est
        # ici qu'on les consulte (aperçu plein écran) et qu'on les importe ou
        # les retire, sans dépendre d'une session (le rattachement à une
        # session est facultatif à l'import).
        grilles = list(Grille.objects
                       .filter(promotion=promotion)
                       .select_related('session')
                       .annotate(nb_ue=Count('ues', distinct=True),
                                 nb_etudiants=Count('lignes', distinct=True))
                       .order_by('-annee_academique', '-importe_le'))

        ctx.update({
            'cours': cours,
            'etudiants': etudiants,
            'nb_avec_antecedents': nb_avec_antecedents,
            'enseignants': enseignants,
            'nb_inscriptions': nb_noted,
            'grilles': grilles,
            'nb_grilles': len(grilles),
        })
        return ctx


class PromotionCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Promotion
    form_class = PromotionForm
    template_name = "app/promotion_form.html"
    success_url = reverse_lazy("promotion_list")
    success_message = "La promotion « %(nom)s » a été créée."


class PromotionUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Promotion
    form_class = PromotionForm
    template_name = "app/promotion_form.html"
    success_url = reverse_lazy("promotion_list")
    success_message = "La promotion « %(nom)s » a été modifiée."


class PromotionDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Promotion
    template_name = "app/promotion_confirm_delete.html"
    success_url = reverse_lazy("promotion_list")
    success_message = "La promotion « %(object)s » a été supprimée."


@login_required
def promotion_bulk_delete(request):
    """Suppression en groupe de promotions sélectionnées."""
    if request.method == 'POST':
        pks = request.POST.getlist('promotion_ids')
        if not pks:
            messages.warning(request, "Aucune promotion n'a été sélectionnée pour la suppression.")
            return redirect('promotion_list')

        with transaction.atomic():
            promotions = Promotion.objects.filter(pk__in=pks)
            count = promotions.count()
            promotions.delete()

        messages.success(request, f"{count} promotion(s) supprimée(s) avec succès.")
    return redirect('promotion_list')


# --- CRUD ENSEIGNANT ---

class EnseignantListView(SafePaginationMixin, ListView):
    model = Enseignant
    template_name = "app/enseignant_list.html"
    context_object_name = "enseignants"
    paginate_by = 25

    def get_queryset(self):
        return (super().get_queryset()
                .annotate(nb_cours=Count('cours', distinct=True))
                .order_by('noms'))


class EnseignantCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Enseignant
    form_class = EnseignantForm
    template_name = "app/enseignant_form.html"
    success_url = reverse_lazy("enseignant_list")
    success_message = "L'enseignant « %(noms)s » a été créé."


class EnseignantUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Enseignant
    form_class = EnseignantForm
    template_name = "app/enseignant_form.html"
    success_url = reverse_lazy("enseignant_list")
    success_message = "L'enseignant « %(noms)s » a été modifié."


class EnseignantDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Enseignant
    template_name = "app/enseignant_confirm_delete.html"
    success_url = reverse_lazy("enseignant_list")
    success_message = "L'enseignant « %(object)s » a été supprimé."


class EnseignantDetailView(DetailView):
    """Fiche détaillée d'un enseignant : cours, promotions et examens."""

    model = Enseignant
    template_name = "app/enseignant_detail.html"
    context_object_name = "enseignant"

    def get_queryset(self):
        return super().get_queryset().prefetch_related(
            Prefetch('cours', queryset=Cours.objects.select_related('promotion')))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        enseignant = self.object
        cours = (enseignant.cours
                 .select_related('promotion')
                 .order_by('promotion__nom', 'nom'))
        promotions = (Promotion.objects
                      .filter(cours__enseignant=enseignant)
                      .distinct()
                      .order_by('nom'))
        examens = (Examen.objects
                   .filter(cours__enseignant=enseignant)
                   .select_related('cours__promotion', 'session')
                   .annotate(nb_inscriptions=Count('inscriptions', distinct=True))
                   .order_by('date_examen'))
        nb_etudiants = (Etudiant.objects
                        .filter(promotion__cours__enseignant=enseignant)
                        .distinct()
                        .count())
        ctx.update({
            'cours': cours,
            'promotions': promotions,
            'examens': examens,
            'nb_etudiants': nb_etudiants,
        })
        return ctx

        return ctx


# --- DETAIL COURS ---
class CoursDetailView(DetailView):
    """Fiche détaillée d'un cours : enseignant, promotion, examens et notes."""

    model = Cours
    template_name = "app/cours_detail.html"
    context_object_name = "cours"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cours = self.object
        examens = (Examen.objects
                   .filter(cours=cours)
                   .select_related('session', 'cours__promotion',
                                   'cours__enseignant')
                   .annotate(nb_inscriptions=Count('inscriptions', distinct=True))
                   .order_by('date_examen'))
        inscriptions = (Inscription.objects
                        .filter(examen__cours=cours)
                        .select_related('etudiant__promotion', 'examen',
                                        'examen__session')
                        .order_by('examen__date_examen', 'etudiant__noms'))
        # Moyenne du cours : moyenne des moyennes des inscriptions notées.
        notes = [i.note for i in inscriptions if i.note is not None]
        moyenne = round(sum(notes) / len(notes), 2) if notes else None
        nb_etudiants = (Etudiant.objects
                        .filter(inscriptions__examen__cours=cours)
                        .distinct()
                        .count())
        ctx.update({
            'examens': examens,
            'inscriptions': inscriptions,
            'moyenne': moyenne,
            'nb_etudiants': nb_etudiants,
        })
        return ctx


@login_required
def cours_bulk_delete(request):
    """Suppression en groupe de cours sélectionnés."""
    if request.method == 'POST':
        pks = request.POST.getlist('cours_ids')
        if not pks:
            messages.warning(request, "Aucun cours n'a été sélectionné pour la suppression.")
            return redirect('cours_list')

        with transaction.atomic():
            cours_qs = Cours.objects.filter(pk__in=pks)
            count = cours_qs.count()
            cours_qs.delete()

        messages.success(request, f"{count} cours supprimé(s) avec succès.")
    return redirect('cours_list')


# --- CRUD SESSION ---

class SessionListView(SafePaginationMixin, ListView):
    model = Session
    template_name = "app/session_list.html"
    context_object_name = "sessions"
    paginate_by = 25


def _donnees_participants_session(session):
    """Données partagées par la vue détail et l'export PDF des participants.

    Retourne (examens, participants_par_promotion, nb_inscriptions,
    nb_participants) : les participants sont les étudiants uniques inscrits
    à au moins un examen de la session, regroupés par promotion.
    """
    examens = (session.examens
               .select_related('cours__promotion', 'cours__enseignant')
               .prefetch_related(Prefetch(
                   'inscriptions',
                   queryset=Inscription.objects
                   .select_related('etudiant')
                   .order_by('etudiant__noms')))
               .order_by('date_examen', 'cours__nom'))
    participants_ids = set()
    nb_inscriptions = 0
    for examen in examens:
        inscriptions = examen.inscriptions.all()  # préchargé (Prefetch)
        nb_inscriptions += len(inscriptions)
        participants_ids.update(ins.etudiant_id for ins in inscriptions)

    participants = (Etudiant.objects
                    .filter(pk__in=participants_ids)
                    .select_related('promotion')
                    .annotate(nb_examens=Count(
                        'inscriptions',
                        filter=Q(inscriptions__examen__session=session)))
                    .order_by('promotion__nom', 'noms'))

    groupes = OrderedDict()
    for etu in participants:
        groupes.setdefault(etu.promotion, []).append(etu)
    return examens, list(groupes.items()), nb_inscriptions, len(participants_ids)


def _donnees_grille(grille):
    """Données d'une grille, prêtes pour l'affichage.

    Factorise la préparation commune à l'onglet « Grilles » du détail de
    session et à la page d'aperçu : UE triées, notes indexées par
    (ligne, UE), et pour chaque ligne la liste des notes dans l'ordre exact
    des colonnes.
    """
    ues = list(grille.ues.order_by('semestre', 'ordre', 'intitule'))
    notes = {
        (note.ligne_id, note.ue_id): note
        for note in GrilleNote.objects.filter(ligne__grille=grille)
    }
    lignes = []
    for ligne in grille.lignes.select_related('etudiant'):
        lignes.append({
            'ligne': ligne,
            'notes': [notes.get((ligne.pk, ue.pk)) for ue in ues],
        })
    return {
        'grille': grille,
        'ues': ues,
        'ues_s1': [ue for ue in ues if ue.semestre == 1],
        'ues_s2': [ue for ue in ues if ue.semestre == 2],
        'lignes': lignes,
    }


def _grilles_session(session):
    """Grilles d'une session, préparées pour l'affichage.

    Les structures sont construites ici plutôt que dans le gabarit : un
    traducteur Django ne peut pas indexer un dictionnaire de notes, et un
    accès `ligne.notes.get(ue)` en template déclencherait une requête par
    cellule (30 UE × N étudiants). Trois requêtes par grille suffisent.

    Retourne une liste de dictionnaires : `grille`, `ues` (triées),
    `ues_s1`, `ues_s2` et `lignes` — chaque ligne portant ses notes dans
    l'ordre exact des colonnes.
    """
    grilles = (Grille.objects
               .filter(session=session)
               .select_related('promotion')
               .order_by('promotion__nom'))
    return [_donnees_grille(grille) for grille in grilles]


class SessionDetailView(DetailView):
    """Détail d'une session : examens programmés et participants.

    Les participants d'un examen sont ses inscriptions (modèle Inscription).
    L'onglet « Participants » regroupe les étudiants uniques de la session par
    promotion ; l'onglet « Cours » liste les examens programmés.
    """

    model = Session
    template_name = "app/session_detail.html"
    context_object_name = "session"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session = self.object
        examens, groupes, nb_inscriptions, nb_participants = \
            _donnees_participants_session(session)
        participants = [etu for _, etus in groupes for etu in etus]

        # Les grilles sont rattachées à la session à l'import. L'onglet
        # « Grilles » liste chaque promotion de la session avec sa grille
        # (ou un bouton d'import si elle n'existe pas encore).
        grilles = _grilles_session(session)
        grille_par_promotion = {
            element['grille'].promotion_id: element for element in grilles
        }
        promotions_session = list(
            Promotion.objects
            .filter(cours__examens__session=session)
            .distinct().order_by('nom'))
        promotions_grilles = [
            {'promotion': promotion,
             'grille': grille_par_promotion.get(promotion.pk)}
            for promotion in promotions_session
        ]

        ctx.update({
            'examens': examens,
            'nb_inscriptions': nb_inscriptions,
            'nb_participants': nb_participants,
            'participants': participants,
            'participants_par_promotion': groupes,
            'grilles': grilles,
            'nb_grilles': len(grilles),
            'promotions_grilles': promotions_grilles,
            'promotions_sans_grille': [
                item['promotion'] for item in promotions_grilles
                if item['grille'] is None
            ],
        })
        return ctx


@login_required
def session_participants_pdf(request, pk):
    """Exporte en PDF la liste des participants d'une session.

    Une section par promotion (étudiants uniques de la session), avec
    n°, n° étudiant, nom et nombre d'examens suivis — même contenu que
    l'onglet « Participants » du détail de la session.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    session = get_object_or_404(Session, pk=pk)
    _, groupes, _, nb_participants = _donnees_participants_session(session)
    tampon = BytesIO()
    doc = SimpleDocTemplate(
        tampon, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"Participants - {session.nom}")
    styles = getSampleStyleSheet()
    elements = [
        Paragraph(f"Liste des participants — {session.nom}",
                  styles['Title']),
        Paragraph(
            f"{session.get_semestre_display()} · "
            f"{session.get_type_session_display()} · "
            f"du {session.date_debut:%d/%m/%Y} au {session.date_fin:%d/%m/%Y} · "
            f"{nb_participants} participant(s)",
            styles['Normal']),
        Spacer(1, 6 * mm),
    ]
    if not groupes:
        elements.append(Paragraph(
            "Aucun participant inscrit pour cette session.", styles['Normal']))
    for promotion, etudiants in groupes:
        elements.append(Paragraph(
            f"{promotion.nom} — {len(etudiants)} participant(s)",
            styles['Heading2']))
        lignes = [['N°', 'Nom et prénoms', 'Examens']]
        for i, etu in enumerate(etudiants, start=1):
            lignes.append([
                str(i), f"{etu.noms}", str(etu.nb_examens)])
        tableau = Table(lignes, colWidths=[12 * mm, 132 * mm, 20 * mm])
        tableau.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#212529')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (-1, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [colors.white, colors.HexColor('#f2f2f2')]),
        ]))
        elements.append(tableau)
        elements.append(Spacer(1, 5 * mm))
    doc.build(elements)
    tampon.seek(0)
    return FileResponse(
        tampon, as_attachment=True, content_type='application/pdf',
        filename=f"participants-{slugify(session.nom)}.pdf")


def _cle_annee(annee):
    """Clé de tri d'une année académique (« 2025-2026 » -> [2025, 2026])."""
    return [int(partie) for partie in (annee or '').replace(' ', '').split('-')
            if partie.isdigit()]


def _donnees_ues_a_reprendre(etudiant):
    """UE à reprendre d'un étudiant, toutes années confondues.

    Réutilise `_ues_a_reprendre` (la règle même de la délibération : note
    sous 10/20 ou UE non notée) sur *toutes* les grilles fréquentées
    par l'étudiant — promotion courante comme années antérieures — puis
    aplatit le résultat en une entrée par UE échouée, prête pour
    l'impression : `annee_academique`, `promotion`, `ue`, `note` et `ligne`
    (décision et moyenne délibérées).

    Retourne `(elements, nb_credits)`.
    """
    grille_lignes = list(
        GrilleEtudiant.objects
        .select_related('grille', 'grille__promotion')
        .filter(etudiant=etudiant)
        .order_by('-grille__annee_academique', '-grille__id'))
    elements = []
    for groupe in _ues_a_reprendre(grille_lignes):
        for note in groupe['ues']:
            elements.append({
                'annee_academique': groupe['grille'].annee_academique,
                'promotion': groupe['grille'].promotion,
                'ue': note.ue,
                'note': note,
                'ligne': groupe['ligne'],
            })
    return elements, sum(element['ue'].credits for element in elements)


@login_required
def etudiant_ues_reprendre_pdf(request, pk):
    """PDF dédié : uniquement les UE à reprendre, toutes années confondues.

    L'en-tête rappelle l'étudiant, sa promotion actuelle et le total des
    crédits à repasser ; le corps liste, année par année (la plus récente
    d'abord) et par promotion, les UE non acquises — note sous 10/20 ou UE
    non notée — avec leur note /20 sans décimale, leur semestre, leurs
    crédits et la décision délibérée. Un total de crédits clôt chaque
    année : c'est la liste à remettre à l'étudiant pour ses rattrapages.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    etudiant = get_object_or_404(
        Etudiant.objects.select_related('promotion'), pk=pk)
    elements_ues, nb_credits = _donnees_ues_a_reprendre(etudiant)

    # Regroupement par (année, promotion) : une ligne de délibération
    # appartient à une promotion donnée pour une année donnée.
    sections = OrderedDict()
    for element in elements_ues:
        clef = (element['annee_academique'], element['promotion'].nom)
        sections.setdefault(clef, []).append(element)

    tampon = BytesIO()
    doc = SimpleDocTemplate(
        tampon, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"UE a reprendre - {etudiant.noms}")
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("UE à reprendre", styles['Title']),
        Paragraph(
            f"{etudiant.noms} · {etudiant.promotion.nom}", styles['Normal']),
    ]
    if elements_ues:
        annees = []
        for annee, _ in sections:
            if annee not in annees:
                annees.append(annee)
        annees.sort(key=_cle_annee, reverse=True)
        elements.append(Paragraph(
            f"{len(elements_ues)} UE · {nb_credits} crédit(s) à reprendre · "
            f"{len(annees)} année(s) : "
            f"{', '.join(annee or '—' for annee in annees)}",
            styles['Normal']))
    else:
        elements.append(Paragraph(
            "Aucune UE à reprendre sur l'ensemble des années.",
            styles['Normal']))
    elements.append(Spacer(1, 6 * mm))

    if elements_ues:
        en_tete = ['UE', 'Cr.', 'Note', 'Sem.', 'Décision']
        for (annee, promotion_nom), lignes_annee in sorted(
                sections.items(), key=lambda item: _cle_annee(item[0][0]),
                reverse=True):
            elements.append(Paragraph(
                f"{annee or '—'} — {promotion_nom}", styles['Heading2']))
            lignes = [en_tete[:]]
            for element in lignes_annee:
                note = element['note']
                lignes.append([
                    element['ue'].intitule,
                    str(element['ue'].credits),
                    (f"{note.note_affichee}/20" if note.est_notee
                     else 'Non notée'),
                    str(element['ue'].semestre),
                    element['ligne'].decision or '—',
                ])
            lignes.append([
                'Total',
                str(sum(item['ue'].credits for item in lignes_annee)),
                '', '', ''])
            tableau = Table(
                lignes, colWidths=[92 * mm, 14 * mm, 18 * mm, 16 * mm,
                                   20 * mm])
            tableau.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#212529')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f2f2f2')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2),
                 [colors.white, colors.HexColor('#fafafa')]),
            ]))
            elements.append(tableau)
            elements.append(Spacer(1, 5 * mm))

    doc.build(elements)
    tampon.seek(0)
    # `as_attachment=False` : le PDF s'ouvre dans l'onglet (donc
    # immédiatement imprimable) au lieu d'être téléchargé — c'est un
    # document de travail à remettre à l'étudiant, pas une archive.
    return FileResponse(
        tampon, as_attachment=False, content_type='application/pdf',
        filename=f"ues-reprendre-{slugify(etudiant.noms)}.pdf")


def session_grille_apercu(request, pk, grille_pk):
    """Aperçu plein écran d'une grille de délibération (sans sidebar).

    L'onglet « Grilles » du détail de session liste les promotions dotées
    d'une grille ; un clic ouvre ici le tableau complet de la promotion —
    notes sans décimale superflue, moyennes et délibération — dans un gabarit
    autonome (pas d'héritage de `base.html`, donc pas de sidebar).
    """
    session = get_object_or_404(Session, pk=pk)
    grille = get_object_or_404(
        Grille.objects.select_related('promotion', 'session'),
        pk=grille_pk, session=session)
    return render(request, 'app/grille_apercu.html', {
        'session': session,
        **_donnees_grille(grille),
    })


@login_required
def session_grille_retirer(request, pk, grille_pk):
    """Retire la grille d'une promotion de la session (POST uniquement).

    La suppression cascade vers les UE, les lignes étudiants et les notes.
    Le GET est refusé : retirer une grille délibérée doit être un geste
    explicite, jamais un crawler ni un préchargement de lien.
    """
    session = get_object_or_404(Session, pk=pk)
    grille = get_object_or_404(
        Grille, pk=grille_pk, session=session)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    promotion_nom = grille.promotion.nom
    grille.delete()
    messages.success(
        request,
        f"La grille de {promotion_nom} a été retirée de la session "
        f"« {session.nom} ».")
    return redirect('session_detail', pk=pk)


@login_required
def promotion_grille_apercu(request, pk, grille_pk):
    """Aperçu plein écran d'une grille, depuis la fiche de la promotion.

    Même gabarit autonome que l'aperçu d'une session (pas de sidebar), mais
    l'accès passe par la promotion : une grille peut exister sans être
    rattachée à une session (le lien de session est facultatif à l'import).
    """
    promotion = get_object_or_404(Promotion, pk=pk)
    grille = get_object_or_404(
        Grille.objects.select_related('promotion', 'session'),
        pk=grille_pk, promotion=promotion)
    return render(request, 'app/grille_apercu.html', {
        'session': grille.session,
        'promotion': promotion,
        **_donnees_grille(grille),
    })


@login_required
def promotion_grille_retirer(request, pk, grille_pk):
    """Retire (supprime) une grille depuis la fiche de la promotion.

    Suppression en cascade vers les UE, les lignes étudiants et les notes.
    Le GET est refusé, comme pour le retrait depuis une session : retirer une
    grille délibérée doit rester un geste explicite.
    """
    promotion = get_object_or_404(Promotion, pk=pk)
    grille = get_object_or_404(Grille, pk=grille_pk, promotion=promotion)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    annee = grille.annee_academique
    grille.delete()
    messages.success(
        request, f'La grille {annee} de {promotion.nom} a été retirée.')
    return redirect('promotion_detail', pk=pk)


class SessionCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Session
    form_class = SessionForm
    template_name = "app/session_form.html"
    success_url = reverse_lazy("session_list")
    success_message = "La session « %(nom)s » a été créée."


class SessionUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Session
    form_class = SessionForm
    template_name = "app/session_form.html"
    success_url = reverse_lazy("session_list")
    success_message = "La session « %(nom)s » a été modifiée."


class SessionDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Session
    template_name = "app/session_confirm_delete.html"
    success_url = reverse_lazy("session_list")
    success_message = "La session « %(object)s » a été supprimée."


@login_required
def session_definir_active(request, pk):
    """Marque une session comme « session en cours » (une seule à la fois).

    Simple bascule : le premier POST active la session (et désactive les
    autres via Session.save), le suivant retire la mention.
    """
    session = get_object_or_404(Session, pk=pk)
    if request.method == 'POST':
        session.est_active = not session.est_active
        session.save()
        if session.est_active:
            messages.success(
                request,
                f"La session « {session.nom} » est désormais la session en cours.")
        else:
            messages.info(
                request,
                f"La session « {session.nom} » n'est plus marquée "
                "comme session en cours.")
    return redirect('session_list')


# --- CRUD COURS ---
class CoursListView(SafePaginationMixin, ListView):
    """Liste des cours, filtrable par recherche, promotion et enseignant."""
    model = Cours
    template_name = "app/cours_list.html"
    context_object_name = "cours_list"
    paginate_by = 25

    def get_queryset(self):
        qs = Cours.objects.select_related('enseignant', 'promotion')
        params = self.request.GET
        self.filters = {}

        q = params.get('q', '').strip()
        if q:
            qs = qs.filter(nom__icontains=q)
            self.filters['q'] = q

        promo = params.get('promotion', '').strip()
        if promo.isdigit():
            qs = qs.filter(promotion__pk=promo)
            self.filters['promotion'] = int(promo)

        ens = params.get('enseignant', '').strip()
        if ens.isdigit():
            qs = qs.filter(enseignant__pk=ens)
            self.filters['enseignant'] = int(ens)

        return qs.order_by('promotion__nom', 'nom')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Options des filtres : uniquement les valeurs réellement utilisées
        context['filter_promotions'] = (Promotion.objects
                                        .filter(cours__isnull=False)
                                        .distinct().order_by('nom'))
        context['filter_enseignants'] = (Enseignant.objects
                                         .filter(cours__isnull=False)
                                         .distinct().order_by('noms'))
        context['filters'] = self.filters
        context['total_cours'] = Cours.objects.count()
        return context


class CoursCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Cours
    form_class = CoursForm
    template_name = "app/cours_form.html"
    success_url = reverse_lazy("cours_list")
    success_message = "Le cours « %(nom)s » a été créé."


class CoursUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Cours
    form_class = CoursForm
    template_name = "app/cours_form.html"
    success_url = reverse_lazy("cours_list")
    success_message = "Le cours « %(nom)s » a été modifié."


class CoursDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Cours
    template_name = "app/cours_confirm_delete.html"
    success_url = reverse_lazy("cours_list")
    success_message = "Le cours « %(object)s » a été supprimé."


# --- CRUD EXAMEN ---
class ExamenListView(SafePaginationMixin, ListView):
    """Liste des examens, filtrable par recherche, promotion, enseignant et session."""
    model = Examen
    template_name = "app/examen_list.html"
    context_object_name = "examens"
    paginate_by = 25

    def get_queryset(self):
        qs = (Examen.objects
              .select_related('cours__promotion', 'cours__enseignant', 'session')
              .annotate(nb_inscriptions=Count('inscriptions', distinct=True)))
        params = self.request.GET
        self.filters = {}
        self.tri = params.get('tri', 'cours').strip() or 'cours'

        q = params.get('q', '').strip()
        if q:
            qs = qs.filter(Q(cours__nom__icontains=q)
                           | Q(cours__promotion__nom__icontains=q)
                           | Q(cours__enseignant__noms__icontains=q)
                           | Q(session__nom__icontains=q))
            self.filters['q'] = q

        promo = params.get('promotion', '').strip()
        if promo.isdigit():
            qs = qs.filter(cours__promotion__pk=promo)
            self.filters['promotion'] = int(promo)

        ens = params.get('enseignant', '').strip()
        if ens.isdigit():
            qs = qs.filter(cours__enseignant__pk=ens)
            self.filters['enseignant'] = int(ens)

        # Session : par défaut, la liste est limitée à la session en cours
        # (si elle existe) ; « 0 » affiche toutes les sessions ; « en-cours »
        # restreint explicitement à la session active. Les attributs exposés
        # au template servent à la sélection du filtre et au bandeau d'info.
        self.session_active = Session.objects.filter(est_active=True).first()
        self.session_defaut = False
        self.session_choisie = False
        session = params.get('session', '').strip()
        if session == '0':
            self.filters['session'] = 'toutes'
        elif session == 'en-cours':
            if self.session_active:
                qs = qs.filter(session=self.session_active)
                self.session_choisie = True
        elif session.isdigit():
            qs = qs.filter(session__pk=session)
            self.filters['session'] = int(session)
        elif self.session_active:
            qs = qs.filter(session=self.session_active)
            self.session_defaut = True

        statut = params.get('statut', '').strip()
        if statut == 'note':
            qs = qs.filter(est_note=True)
            self.filters['statut'] = 'note'
        elif statut == 'non-note':
            qs = qs.filter(est_note=False)
            self.filters['statut'] = 'non-note'
        elif statut == 'a-venir':
            qs = qs.filter(date_examen__gte=timezone.now())
            self.filters['statut'] = 'a-venir'

        tris_autorises = {
            'cours': ('cours__nom', 'date_examen'),
            '-cours': ('-cours__nom', '-date_examen'),
            'promotion': ('cours__promotion__nom', 'cours__nom', 'date_examen'),
            '-promotion': ('-cours__promotion__nom', '-cours__nom', '-date_examen'),
            'session': ('session__nom', 'cours__nom', 'date_examen'),
            '-session': ('-session__nom', '-cours__nom', '-date_examen'),
            'date': ('date_examen', 'cours__nom'),
            '-date': ('-date_examen', '-cours__nom'),
        }
        if self.tri not in tris_autorises:
            self.tri = 'cours'
        self.filters['tri'] = self.tri
        return qs.order_by(*tris_autorises[self.tri])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Statistiques d'en-tête : total, notés, en attente, à venir
        # (une seule requête d'agrégation, indépendante des filtres).
        context['stats_examens'] = Examen.objects.aggregate(
            total=Count('id'),
            notes=Count('id', filter=Q(est_note=True)),
            en_attente=Count('id', filter=Q(est_note=False)),
            a_venir=Count('id', filter=Q(date_examen__gte=timezone.now())),
        )
        # Options des filtres : uniquement les valeurs réellement utilisées
        context['filter_promotions'] = (Promotion.objects
                                        .filter(cours__examens__isnull=False)
                                        .distinct().order_by('nom'))
        context['filter_enseignants'] = (Enseignant.objects
                                         .filter(cours__examens__isnull=False)
                                         .distinct().order_by('noms'))
        context['filter_sessions'] = (Session.objects
                                      .filter(examens__isnull=False)
                                      .distinct().order_by('date_debut', 'nom'))
        context['filters'] = self.filters
        # Chaîne de requête sans « tri » ni « page », pour les liens de tri
        # qui conservent les filtres courants.
        params = self.request.GET.copy()
        params.pop('tri', None)
        params.pop('page', None)
        context['base_qs'] = params.urlencode()
        context['tri'] = self.tri
        context['total_examens'] = Examen.objects.count()
        context['session_active'] = getattr(self, 'session_active', None)
        context['session_defaut'] = getattr(self, 'session_defaut', False)
        context['session_choisie'] = getattr(self, 'session_choisie', False)
        return context


class ExamenCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    """Aucune inscription pré-créée : la fiche de cote se construit
    dynamiquement à partir des étudiants de la promotion du cours.
    Une ligne d'inscription n'est créée en base que lorsqu'une note est saisie.
    """
    model = Examen
    form_class = ExamenForm
    template_name = "app/examen_form.html"
    success_url = reverse_lazy("examen_list")
    success_message = "L'examen a été créé."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['cours_data'] = list(Cours.objects
                                 .select_related('promotion', 'enseignant')
                                 .order_by('promotion__nom', 'nom')
                                 .values_list('id', 'nom', 'promotion__nom',
                                              'enseignant__noms'))
        return ctx


class ExamenUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Examen
    form_class = ExamenForm
    template_name = "app/examen_form.html"
    success_url = reverse_lazy("examen_list")
    success_message = "L'examen a été modifié."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['cours_data'] = list(Cours.objects
                                 .select_related('promotion', 'enseignant')
                                 .order_by('promotion__nom', 'nom')
                                 .values_list('id', 'nom', 'promotion__nom',
                                              'enseignant__noms'))
        examen = self.object
        ctx['inscriptions'] = (examen.inscriptions
                               .select_related('etudiant')
                               .order_by('etudiant__noms'))
        ctx['inscriptibles'] = (Etudiant.objects
                                .filter(promotion=examen.cours.promotion)
                                .exclude(pk__in=examen.inscriptions
                                         .values_list('etudiant_id', flat=True))
                                .order_by('noms'))
        return ctx


class ExamenDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Examen
    template_name = "app/examen_confirm_delete.html"
    success_url = reverse_lazy("examen_list")
    success_message = "L'examen « %(object)s » a été supprimé."


@login_required
def fiche_cote(request, pk):
    """Fiche de cote d'un examen (modèle du PDF « Fiche_ETHIQUE & DEONTOLOGIE »).

    Vue en lecture seule, destinée à être imprimée : les notes sont saisies
    manuscritement sur la fiche papier. Les étudiants sont inscrits au fur
    et à mesure (bouton « Ajouter un étudiant »), la fiche n'affiche que les
    étudiants effectivement inscrits.
    """
    examen = get_object_or_404(
        Examen.objects.select_related('cours__promotion', 'cours__enseignant', 'session'),
        pk=pk)
    inscriptions = (examen.inscriptions
                    .select_related('etudiant')
                    .order_by('etudiant__noms'))

    inscrits_ids = inscriptions.values_list('etudiant_id', flat=True)
    inscriptibles = (Etudiant.objects
                     .filter(promotion=examen.cours.promotion)
                     .exclude(pk__in=inscrits_ids)
                     .order_by('noms'))
    return render(request, 'app/fiche_cote.html', {
        'examen': examen,
        'inscriptions': inscriptions,
        'inscriptibles': inscriptibles,
        'titulaire': examen.cours.enseignant,
    })


@login_required
def examen_inscrire(request, pk):
    """Inscrit un ou plusieurs étudiants (de la promotion du cours) à un examen.

    Les étudiants sont choisis via des cases à cocher (champ POST
    « etudiants », multiples) ; le champ « etudiant » unique reste accepté.
    « next » permet de revenir à la page d'édition plutôt qu'à la fiche.
    """
    examen = get_object_or_404(Examen.objects.select_related('cours'), pk=pk)
    if request.method == 'POST':
        pks = [v for v in request.POST.getlist('etudiants')
               if v.isdigit()]
        if not pks and request.POST.get('etudiant', '').isdigit():
            pks = [request.POST['etudiant']]
        if not pks:
            messages.warning(request, "Aucun étudiant sélectionné.")
            return redirect(_retour_examen(request, examen))
        existants = set(examen.inscriptions.values_list('etudiant_id', flat=True))
        candidats = (Etudiant.objects
                     .filter(pk__in=[int(v) for v in pks],
                             promotion=examen.cours.promotion)
                     .exclude(pk__in=existants))
        crees = Inscription.objects.bulk_create(
            [Inscription(examen=examen, etudiant=e) for e in candidats])
        deja = len(pks) - len(candidats)
        if crees:
            messages.success(
                request,
                f"{len(crees)} étudiant(s) ajouté(s) à la fiche de cote.")
        if deja > 0:
            messages.info(request, f"{deja} étudiant(s) figuraient déjà sur la fiche.")
        if not crees and deja == 0:
            messages.warning(request, "Aucun étudiant ajouté.")
    return redirect(_retour_examen(request, examen))


@login_required
def examen_inscrire_tous(request, pk):
    """Inscrit d'un coup tous les étudiants de la promotion du cours
    qui ne sont pas encore sur la fiche de cote."""
    examen = get_object_or_404(Examen.objects.select_related('cours__promotion'), pk=pk)
    if request.method == 'POST':
        existants = examen.inscriptions.values_list('etudiant_id', flat=True)
        manquants = (Etudiant.objects
                     .filter(promotion=examen.cours.promotion)
                     .exclude(pk__in=existants))
        crees = Inscription.objects.bulk_create(
            [Inscription(examen=examen, etudiant=e) for e in manquants])
        if crees:
            messages.success(
                request,
                f"{len(crees)} étudiant(s) de la promotion "
                f"« {examen.cours.promotion.nom} » ajouté(s) à la fiche.")
        else:
            messages.info(request, "Tous les étudiants de la promotion figurent déjà sur la fiche.")
    return redirect(_retour_examen(request, examen))


@login_required
def examen_desinscrire(request, pk, inscription_pk):
    """Retire un étudiant d'une fiche de cote (supprime aussi ses notes)."""
    examen = get_object_or_404(Examen, pk=pk)
    if request.method == 'POST':
        inscription = get_object_or_404(Inscription, pk=inscription_pk,
                                        examen=examen)
        nom = str(inscription.etudiant)
        inscription.delete()
        messages.success(request, f"{nom} a été retiré de la fiche de cote.")
    return redirect(_retour_examen(request, examen))


def _retour_examen(request, examen):
    """Page de retour après inscription/désinscription : fiche par défaut,
    page d'édition si « next » pointe dessus (évite toute redirection ouverte)."""
    nxt = request.POST.get('next', '')
    if nxt.startswith(f'/examens/{examen.pk}/edit/'):
        return nxt
    return f'/examens/{examen.pk}/fiche/'


@login_required
def examen_marquer_notee(request, pk):
    """Signale qu'un examen est noté (ou retire cette mention), depuis la liste.

    Une simple bascule : le premier POST coche « est_note », le suivant le
    décoche. « next » (envoyé par le formulaire de la liste) permet de revenir
    sur la même page en conservant filtres, tri et pagination ; toute valeur
    ne commençant pas par « /examens/ » (redirection ouverte) est ignorée.
    """
    examen = get_object_or_404(Examen, pk=pk)
    if request.method == 'POST':
        examen.est_note = not examen.est_note
        examen.save(update_fields=['est_note'])
        libelle = f"l'examen « {examen.cours.nom} » ({examen.session})"
        if examen.est_note:
            messages.success(request, f"{libelle} a été signalé comme noté.")
        else:
            messages.info(request, f"{libelle} n'est plus marqué comme noté.")
        nxt = request.POST.get('next', '')
        if nxt.startswith('/examens/'):
            return HttpResponseRedirect(nxt)
    return redirect('examen_list')


def examen_non_notes_impression(request):
    """Page imprimable : liste des cours (examens) dont les copies ne sont
    pas encore corrigées (est_note=False), tous confondus, triés par
    enseignant (noms), les cours sans titulaire en dernier.

    Document de suivi pour le secrétariat / chef de département : tableau
    compact, total et zone de signature, conçu pour l'impression (même
    démarche que la fiche de cote).
    """
    examens = (Examen.objects
               .select_related('cours__promotion', 'cours__enseignant', 'session')
               .filter(est_note=False)
               .order_by(F('cours__enseignant__noms').asc(nulls_last=True),
                         'cours__nom', 'cours__promotion__nom'))
    return render(request, 'app/examen_non_notes_print.html',
                  {'examens': examens, 'total': examens.count()})


@login_required
def import_excel_view(request):
    """Vue pour l'importation de fichiers Excel (Étudiants, Enseignants, Cours,
    Grilles de délibération)."""
    type_param = request.GET.get('type', 'etudiants')
    if type_param not in ['etudiants', 'enseignants', 'cours', 'grilles']:
        type_param = 'etudiants'

    resultats = None

    if request.method == 'POST':
        form = ExcelImportForm(request.POST, request.FILES)
        if form.is_valid():
            type_import = form.cleaned_data['type_import']
            fichier = form.cleaned_data['fichier_excel']
            promotion = form.cleaned_data.get('promotion')
            promo_id = promotion.pk if promotion else None

            if type_import == 'etudiants':
                res = import_etudiants_excel(fichier, default_promotion_id=promo_id)
                libelle = "étudiants"
            elif type_import == 'enseignants':
                res = import_enseignants_excel(fichier)
                libelle = "enseignants"
            elif type_import == 'cours':
                res = import_cours_excel(fichier, default_promotion_id=promo_id)
                libelle = "cours"
            elif type_import == 'grilles':
                # Une grille est rattachée à *une* promotion et, si elle est
                # fournie, à *une* session : c'est ce lien qui l'affiche dans
                # le détail de la session. L'année saisie prime sur celle lue
                # dans le titre du fichier (cas des grilles d'années
                # antérieures, ex. L1 2024-2025 des actuels L2).
                annee = (form.cleaned_data.get('annee_academique') or '').strip()
                res = import_grille_excel(
                    fichier, promotion, form.cleaned_data.get('session'),
                    annee_academique=annee or None)
                libelle = "grilles"
            else:
                res = {'success': False, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': ["Type d'import invalide."]}
                libelle = "éléments"

            resultats = res
            if res['success']:
                if type_import == 'grilles':
                    msg = (f"Import de la grille {promotion} terminé : "
                           f"{res['nb_ue']} UE, {res['nb_lignes']} étudiant(s), "
                           f"{res['nb_notes']} note(s)")
                    msg += (". Grille mise à jour." if res['updated']
                            else ". Nouvelle grille enregistrée.")
                    if res.get('rattaches_hors_promotion'):
                        # Grille d'une année antérieure importée après le passage
                        # en promotion supérieure : ces lignes alimentent
                        # l'historique des années antérieures des fiches actuelles.
                        msg += (f" ({res['rattaches_hors_promotion']} ligne(s) "
                                "rattachée(s) à des fiches d'une autre "
                                "promotion — historique des années antérieures).")
                    if res.get('archivees'):
                        # Cohorte passée : lignes archivées telles quelles,
                        # sans fiche rattachée ni étape de parcours.
                        msg += (f" ({res['archivees']} ligne(s) archivée(s) "
                                "sans rattachement — cohorte d'une année "
                                "antérieure).")
                else:
                    msg = f"Import {libelle} terminé : {res['created']} créé(s), {res['updated']} mis à jour, {res['skipped']} ignoré(s)."
                if res['errors']:
                    msg += f" ({len(res['errors'])} avertissement(s)/erreur(s))."
                    messages.warning(request, msg)
                else:
                    messages.success(request, msg)
            else:
                messages.error(request, f"Erreur lors de l'import : {', '.join(res['errors'])}")

            type_param = type_import
    else:
        initial = {'type_import': type_param}
        # Arrivée depuis le détail d'une session ou d'une promotion :
        # la grille est pré-ciblée sur ce contexte.
        session_id = request.GET.get('session')
        if session_id:
            initial['session'] = session_id
        promotion_id = request.GET.get('promotion')
        if promotion_id:
            initial['promotion'] = promotion_id
        form = ExcelImportForm(initial=initial)

    context = {
        'form': form,
        'type_active': type_param,
        'resultats': resultats,
        'promotions': Promotion.objects.all(),
    }
    return render(request, 'app/import_excel.html', context)

