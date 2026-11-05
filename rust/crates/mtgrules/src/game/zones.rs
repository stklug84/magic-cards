//! Zone changes (rules 400, 400.7, 903.9a, 704.5d) and attachment.

use indexmap::IndexMap;

use super::{Game, field};
use crate::events::{Event, EventData, EventType};
use crate::objects::{ObjId, Zone};
use crate::rule;

/// Where in the library a moved card goes.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum LibraryPosition {
    /// the top (the Python default `pos="top"`)
    #[default]
    Top,
    /// the bottom
    Bottom,
}

/// The keyword options of `move_zone`.
#[derive(Clone, Debug, Default)]
pub struct MoveOptions {
    /// enters the battlefield tapped
    pub tapped: bool,
    /// counters the permanent enters with
    pub counters: IndexMap<String, i64>,
    /// library position
    pub pos: LibraryPosition,
}

impl Game {
    /// Move `obj` between zones through the replacement machinery.
    ///
    /// Returns the object, or `None` when the move was prevented or the
    /// object ceased to exist (tokens, rule 704.5d).
    #[rule("400.1", "400.7", "903.9a", "704.5d")]
    pub fn move_zone(&mut self, obj: ObjId, to_zone: Zone) -> Option<ObjId> {
        self.move_zone_with(obj, to_zone, &MoveOptions::default())
    }

    /// [`Game::move_zone`] with the keyword options.
    pub fn move_zone_with(
        &mut self,
        obj: ObjId,
        to_zone: Zone,
        opts: &MoveOptions,
    ) -> Option<ObjId> {
        let (from_zone, controller) = {
            let o = self.obj(obj);
            (o.zone, o.controller)
        };
        let event = self.emit(Event::new(
            EventType::ZoneChange,
            EventData {
                obj: Some(obj),
                from: Some(from_zone),
                to: Some(to_zone),
                tapped: Some(opts.tapped),
                counters: opts.counters.clone(),
                controller: Some(controller),
                ..EventData::default()
            },
        ))?;
        let to_zone = self.commander_redirect(obj, event.data.to.unwrap_or(to_zone));
        self.remove_from_zone(obj, from_zone);

        // rule 704.5d: a token anywhere but the battlefield ceases to exist
        if self.obj(obj).is_token && to_zone != Zone::Battlefield {
            self.token_ceases(obj, from_zone, to_zone, &event);
            return None;
        }

        self.obj_mut(obj).zone = to_zone;
        self.place_in_zone(obj, to_zone, opts.pos);

        if from_zone == Zone::Battlefield || to_zone == Zone::Battlefield {
            // last-known information for leave-the-battlefield triggers
            // (rule 603.10a: they use the object's last existence)
            let (attachments, attached_to, counters) = {
                let o = self.obj(obj);
                (o.attachments.clone(), o.attached_to, o.counters.clone())
            };
            self.obj_mut(obj).lki_counters = counters;
            for att in attachments {
                self.obj_mut(att).attached_to = None;
            }
            if let Some(host) = attached_to {
                let host = self.obj_mut(host);
                if let Some(pos) = host.attachments.iter().position(|a| *a == obj) {
                    host.attachments.remove(pos);
                }
            }
            let timestamp = self.next_timestamp();
            self.obj_mut(obj).reset_battlefield_state(timestamp); // rule 400.7
        }
        if to_zone == Zone::Battlefield {
            {
                let o = self.obj_mut(obj);
                o.entered_this_turn = true;
                o.tapped = event.data.tapped.unwrap_or(false);
                for (kind, n) in &event.data.counters {
                    *o.counters.entry(kind.clone()).or_insert(0) += n;
                }
            }
            self.bump();
            self.queue_triggers(&Event::about(EventType::EntersBattlefield, obj));
        } else if from_zone == Zone::Battlefield {
            self.bump();
            if to_zone == Zone::Graveyard {
                let who = self.name_of(self.obj(obj).controller);
                let card = self.lname_obj(obj);
                self.log("dies", vec![field("who", who), field("card", card)]);
            }
            self.fire_leave_battlefield(obj, &event);
        } else {
            self.bump();
        }
        Some(obj)
    }

    /// Rule 903.9a-b: the owner may send a commander to the command zone.
    #[rule("903.9a")]
    fn commander_redirect(&self, obj: ObjId, to_zone: Zone) -> Zone {
        let o = self.obj(obj);
        if o.commander
            && matches!(
                to_zone,
                Zone::Graveyard | Zone::Exile | Zone::Hand | Zone::Library
            )
            && self
                .policy(o.owner)
                .commander_to_command_zone(self, obj, to_zone)
        {
            Zone::Command
        } else {
            to_zone
        }
    }

    /// Cease a token that left the battlefield (rule 704.5d).
    #[rule("704.5d")]
    fn token_ceases(&mut self, obj: ObjId, from_zone: Zone, to_zone: Zone, event: &Event) {
        self.obj_mut(obj).zone = Zone::Ceased;
        self.bump();
        if from_zone == Zone::Battlefield && to_zone == Zone::Graveyard {
            let controller = self.obj(obj).controller;
            self.player_mut(controller).stat("tokens_killed", 1);
            let who = self.name_of(controller);
            let card = self.lname_obj(obj);
            self.log(
                "dies",
                vec![field("who", who), field("card", card), field("token", true)],
            );
            self.fire_leave_battlefield(obj, event);
        }
    }

    /// Append `obj` to the destination zone's list.
    fn place_in_zone(&mut self, obj: ObjId, to_zone: Zone, pos: LibraryPosition) {
        let holder = if to_zone == Zone::Battlefield {
            self.obj(obj).controller
        } else {
            self.obj(obj).owner
        };
        if to_zone == Zone::Library {
            match pos {
                LibraryPosition::Bottom => self.player_mut(holder).library.push(obj),
                LibraryPosition::Top => self.player_mut(holder).library.insert(0, obj),
            }
        } else {
            self.player_mut(holder).zone_list_mut(to_zone).push(obj);
        }
    }

    /// Queue dies triggers when the destination was a graveyard.
    fn fire_leave_battlefield(&mut self, obj: ObjId, zone_event: &Event) {
        if zone_event.data.to == Some(Zone::Graveyard) {
            self.queue_triggers(&Event::about(EventType::Dies, obj));
        }
    }

    /// Remove `obj` from whichever player's list holds it (nothing for
    /// the stack and for ceased tokens).
    pub(crate) fn remove_from_zone(&mut self, obj: ObjId, zone: Zone) {
        if matches!(zone, Zone::Ceased | Zone::Stack) {
            return;
        }
        for p in &mut self.players {
            let list = p.zone_list_mut(zone);
            if let Some(pos) = list.iter().position(|o| *o == obj) {
                list.remove(pos);
                return;
            }
        }
    }

    /// Attach an Aura/Equipment to its new target (rules 303.4, 301.5).
    #[rule("303.4", "301.5")]
    pub fn attach(&mut self, obj: ObjId, target: ObjId) {
        if let Some(host) = self.obj(obj).attached_to {
            let host = self.obj_mut(host);
            if let Some(pos) = host.attachments.iter().position(|a| *a == obj) {
                host.attachments.remove(pos);
            }
        }
        self.obj_mut(obj).attached_to = Some(target);
        self.obj_mut(target).attachments.push(obj);
        self.bump();
    }
}
